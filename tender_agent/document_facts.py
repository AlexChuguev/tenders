from __future__ import annotations

import re
from pathlib import Path

from tender_agent.analysis_types import ExtractedFacts, FactHit, ParticipantRequirements
from tender_agent.policy import TriagePolicy, load_triage_policy
from tender_agent.triage_rules import build_triage_signals


def extract_document_facts(
    files: list[Path],
    tender_title: str = "",
    tender_price_rub: float | None = None,
    policy: TriagePolicy | None = None,
) -> ExtractedFacts:
    policy = policy or load_triage_policy()
    samples: list[tuple[str, str]] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            text = ""
        if text.strip():
            samples.append((path.name, text[:20000]))

    if not samples:
        return ExtractedFacts(
            procurement_type="не удалось определить",
            key_files=[],
            stack=[],
            turnover_requirements=[],
            project_requirements=[],
            team_requirements=[],
            licenses=[],
            payment=[],
            document_roles={},
            completeness_label="низкая",
            completeness_notes=["нет распознанных документов"],
            triage_signals=[],
            participant_requirements=ParticipantRequirements(),
        )

    combined = "\n".join(text for _, text in samples)
    lower = ru_normalize(combined)
    names = [name for name, _ in samples]
    roles = classify_document_roles(samples)
    stack_text = _select_stack_source_text(samples, roles)
    stack_context = "\n".join([tender_title, *names, stack_text])
    stack = extract_stack_signals(ru_normalize(stack_context))
    turnover = extract_numeric_requirements(lower, TURNOVER_PATTERNS)
    projects = extract_numeric_requirements(lower, PROJECT_PATTERNS)
    team = _normalize_team_requirements(extract_numeric_requirements(lower, TEAM_PATTERNS))
    licenses = extract_license_signals(lower)
    payment = extract_payment_signals(lower)
    participant_requirements = build_participant_requirements(
        lower_text=lower,
        document_roles=roles,
        turnover=turnover,
        projects=projects,
        team=team,
        licenses=licenses,
    )
    procurement_type = detect_procurement_type(ru_normalize(tender_title) + " " + lower, policy=policy)
    completeness_label, completeness_notes = assess_document_completeness(roles, lower)
    triage_signals = build_triage_signals(
        tender_title=tender_title,
        tender_price_rub=tender_price_rub,
        procurement_type=procurement_type,
        stack=stack,
        licenses=licenses,
        lower_text=lower,
        policy=policy,
    )
    facts = ExtractedFacts(
        procurement_type=procurement_type,
        key_files=names[:8],
        stack=stack,
        turnover_requirements=turnover,
        project_requirements=projects,
        team_requirements=team,
        licenses=licenses,
        payment=payment,
        document_roles=roles,
        completeness_label=completeness_label,
        completeness_notes=completeness_notes,
        triage_signals=triage_signals,
        participant_requirements=participant_requirements,
        submission_requirements=participant_requirements.submission_docs,
        quality_flags=[],
    )
    facts.fact_hits = _build_fact_hits(samples, facts)
    return facts


def render_extracted_facts(facts: ExtractedFacts) -> str:
    lines = [
        f"- Тип закупки: {facts.procurement_type}",
        f"- Документная полнота: {facts.completeness_label}" + (
            f" ({'; '.join(facts.completeness_notes[:3])})" if facts.completeness_notes else ""
        ),
    ]
    if facts.key_files:
        lines.append(f"- Вероятно ключевые файлы: {' | '.join(facts.key_files)[:500]}")
    if facts.document_roles:
        rendered_roles = []
        for role in ["тз", "извещение", "требования_к_участнику", "договор", "нмцк", "служебное"]:
            names = facts.document_roles.get(role) or []
            if not names:
                continue
            rendered_roles.append(f"{role}: {', '.join(names[:3])}")
        if rendered_roles:
            lines.append(f"- Роли документов: {'; '.join(rendered_roles)}")
    if facts.stack:
        lines.append(f"- Стек/технологии: {', '.join(facts.stack)}")
    if facts.turnover_requirements:
        lines.append(f"- Финансовые требования: {'; '.join(facts.turnover_requirements[:4])}")
    if facts.project_requirements:
        lines.append(f"- Требования к аналогичным проектам: {'; '.join(facts.project_requirements[:4])}")
    if facts.team_requirements:
        lines.append(f"- Требования к команде/штату: {'; '.join(facts.team_requirements[:4])}")
    if facts.licenses:
        lines.append(f"- Лицензии/допуски: {', '.join(facts.licenses[:6])}")
    if facts.participant_requirements.required_roles:
        lines.append(f"- Роли/специалисты: {', '.join(facts.participant_requirements.required_roles[:6])}")
    if facts.submission_requirements:
        lines.append(f"- Требования к заявке: {'; '.join(facts.submission_requirements[:6])}")
    if facts.payment:
        lines.append(f"- Оплата/обеспечение: {'; '.join(facts.payment[:4])}")
    if facts.triage_signals:
        lines.append(f"- Rule-based triage: {'; '.join(facts.triage_signals[:5])}")
    if facts.quality_flags:
        lines.append(f"- Quality flags: {'; '.join(facts.quality_flags[:5])}")
    return "\n".join(lines)


def format_price_for_prompt(value: float | None) -> str:
    if value is None:
        return "не указана"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}"


def classify_document_roles(samples: list[tuple[str, str]]) -> dict[str, list[str]]:
    roles: dict[str, list[str]] = {}
    for name, text in samples:
        normalized = ru_normalize(name)
        content = ru_normalize(text[:12000])
        matched_roles: list[str] = []

        if any(token in normalized for token in ["тз", "техничес", "spec", "sow"]) or _looks_like_tz(content):
            matched_roles.append("тз")
        if any(token in normalized for token in ["извещ", "документац", "конкурсн", "закупочн"]) or _looks_like_notice(content):
            matched_roles.append("извещение")
        if any(token in normalized for token in ["договор", "контракт", "соглашен"]) or _looks_like_contract(content):
            matched_roles.append("договор")
        if any(token in normalized for token in ["анкет", "участник", "квалифик", "требован"]) or _looks_like_participant_requirements(content):
            matched_roles.append("требования_к_участнику")
        if any(token in normalized for token in ["нмц", "смет", "кп", "цена", "ткп", "калькуляц"]) or _looks_like_price_doc(content):
            matched_roles.append("нмцк")
        if (
            any(token in normalized for token in ["согласие", "реквизит", "форма ответа", "пик", "эдо", "лицензионн"])
            and not matched_roles
        ):
            matched_roles.append("служебное")

        if not matched_roles:
            matched_roles.append("прочее")

        for role in matched_roles:
            roles.setdefault(role, []).append(name)
    return roles


def _select_stack_source_text(
    samples: list[tuple[str, str]],
    roles: dict[str, list[str]],
) -> str:
    if not samples:
        return ""
    name_to_text = {name: text for name, text in samples}
    selected: list[str] = []

    def add_role(role: str) -> None:
        for name in roles.get(role, []):
            if name in name_to_text and name not in selected:
                selected.append(name)

    for role in ["тз", "требования_к_участнику", "извещение", "нмцк", "прочее"]:
        add_role(role)

    if not selected:
        excluded = set(roles.get("служебное", [])) | set(roles.get("договор", []))
        for name, _ in samples:
            if name not in excluded:
                selected.append(name)

    if not selected:
        selected = [name for name, _ in samples]

    return "\n".join(name_to_text[name] for name in selected)


def _looks_like_tz(text: str) -> bool:
    return (
        ("техническ" in text and "задан" in text)
        or ("предмет закупк" in text and "перечень работ" in text)
        or ("описание этапов" in text and "срок оказания услуг" in text)
    )


def _looks_like_notice(text: str) -> bool:
    return (
        "дата окончания подачи заяв" in text
        or "дата и время окончания подачи заяв" in text
        or ("место рассмотрения заявок" in text and "подведения итогов" in text)
    )


def _looks_like_contract(text: str) -> bool:
    return (
        "условия оплаты" in text
        or "авансирован" in text
        or "акт приемк" in text
        or "счет фактур" in text
        or "типовая форма договора" in text
        or "гарантийный срок" in text
    )


def _looks_like_participant_requirements(text: str) -> bool:
    return (
        "требования к участникам" in text
        or "требования к поставщику" in text
        or "требования к исполнителю" in text
        or "требования к подрядчику" in text
        or "квалификационные требования" in text
        or "квалификац" in text
        or "референс лист" in text
        or "наличие разрешений" in text
        or "членство в сро" in text
        or "наличие персонала" in text
    )


def _looks_like_price_doc(text: str) -> bool:
    return (
        "стоимость работ" in text
        or "итого с ндс" in text
        or "калькуляц" in text
        or "обоснован" in text and "нмц" in text
    )


def assess_document_completeness(document_roles: dict[str, list[str]], lower_text: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    score = 0
    if document_roles.get("тз"):
        score += 3
    else:
        notes.append("нет явного ТЗ")
    if document_roles.get("извещение"):
        score += 2
    else:
        notes.append("нет извещения/закупочной документации")
    if document_roles.get("требования_к_участнику") or has_participant_requirements(lower_text):
        score += 2
    else:
        notes.append("требования к участнику выражены слабо")
    if document_roles.get("договор") or extract_payment_signals(lower_text):
        score += 1
    else:
        notes.append("нет явного договора/условий оплаты")
    if score >= 6:
        return "высокая", notes
    if score >= 4:
        return "средняя", notes
    return "низкая", notes


def _infer_submission_requirements(
    document_roles: dict[str, list[str]],
    turnover: list[str],
    projects: list[str],
    team: list[str],
    licenses: list[str],
    lower_text: str,
) -> list[str]:
    required_items: list[str] = ["заявка"]
    if document_roles.get("нмцк"):
        required_items.append("ценовое предложение")
    if projects:
        required_items.append("подтверждение релевантного опыта")
    if turnover:
        required_items.append("финансовые документы/подтверждение оборота")
    if team:
        required_items.append("сведения о команде")
    if licenses:
        required_items.append("подтверждение лицензий/сертификатов")
    if document_roles.get("требования_к_участнику") or has_participant_requirements(lower_text):
        required_items.append("квалификационные документы")
    out: list[str] = []
    seen: set[str] = set()
    for item in required_items:
        key = item.casefold().replace("ё", "е")
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def build_participant_requirements(
    *,
    lower_text: str,
    document_roles: dict[str, list[str]],
    turnover: list[str],
    projects: list[str],
    team: list[str],
    licenses: list[str],
) -> ParticipantRequirements:
    submission_docs = _infer_submission_requirements(document_roles, turnover, projects, team, licenses, lower_text)
    roles = _extract_required_roles(lower_text, team)
    min_turnover_rub = _extract_min_turnover_rub(turnover)
    return ParticipantRequirements(
        requires_sro="СРО" in licenses,
        requires_analog_projects=bool(projects),
        requires_existing_system_modification_experience=_has_existing_system_experience_requirement(lower_text, projects),
        requires_financial_documents=bool(turnover),
        requires_team_documents=bool(team or roles),
        min_turnover_rub=min_turnover_rub,
        turnover_texts=list(turnover),
        experience_texts=list(projects),
        required_roles=roles,
        licenses_or_certs=list(licenses),
        other_requirements=_extract_other_requirement_signals(lower_text),
        submission_docs=submission_docs,
    )


def _extract_min_turnover_rub(turnover: list[str]) -> int | None:
    best: int | None = None
    for item in turnover:
        text = ru_normalize(item)
        match = re.search(r"(\d[\d\s.,]*)\s*(млн|миллион|тыс|руб)?", text)
        if not match:
            continue
        raw = re.sub(r"[\s,]+", "", match.group(1))
        try:
            value = int(float(raw))
        except ValueError:
            continue
        unit = str(match.group(2) or "")
        if unit.startswith("млн") or unit.startswith("миллион"):
            value *= 1_000_000
        elif unit.startswith("тыс"):
            value *= 1_000
        if best is None or value > best:
            best = value
    return best


def _build_fact_hits(samples: list[tuple[str, str]], facts: ExtractedFacts) -> dict[str, list[FactHit]]:
    hits: dict[str, list[FactHit]] = {}

    def add(field: str, term: str, variants: list[str] | None = None) -> None:
        hit = _find_fact_hit(samples, field=field, term=term, variants=variants)
        if not hit:
            return
        bucket = hits.setdefault(field, [])
        key = (hit.term.casefold().replace("ё", "е"), hit.file_name, hit.offset)
        if key in {
            (item.term.casefold().replace("ё", "е"), item.file_name, item.offset)
            for item in bucket
        }:
            return
        bucket.append(hit)

    for item in facts.stack[:8]:
        add("stack", item, _stack_evidence_variants(item))

    req = facts.participant_requirements
    for item in (
        req.turnover_texts[:3]
        + req.experience_texts[:3]
        + req.required_roles[:3]
        + req.licenses_or_certs[:3]
        + req.other_requirements[:3]
        + facts.turnover_requirements[:3]
        + facts.project_requirements[:3]
        + facts.team_requirements[:3]
        + facts.licenses[:3]
    ):
        add("requirements", item, _requirement_evidence_variants(item))

    for item in facts.payment[:4]:
        add("payment", item)

    for role, names in (facts.document_roles or {}).items():
        for name in names[:2]:
            hits.setdefault("documents", []).append(
                FactHit(field="documents", term=role, file_name=name, fragment=f"роль документа: {role}", offset=0)
            )
    return hits


def _find_fact_hit(
    samples: list[tuple[str, str]],
    *,
    field: str,
    term: str,
    variants: list[str] | None = None,
) -> FactHit | None:
    needles = [term, *(variants or [])]
    normalized_needles = _dedupe_texts(
        [ru_normalize(item) for item in needles if str(item or "").strip()]
    )
    for needle in normalized_needles:
        if not needle:
            continue
        for file_name, text in samples:
            normalized_text = ru_normalize(text)
            pos = normalized_text.find(needle)
            if pos < 0:
                continue
            original_pos = _approx_original_offset(text, normalized_text, pos)
            fragment = re.sub(
                r"\s+",
                " ",
                text[max(0, original_pos - 80) : original_pos + len(needle) + 120],
            ).strip(" ,;.")
            page = _infer_page_from_text_offset(text, original_pos)
            return FactHit(
                field=field,
                term=term,
                file_name=file_name,
                fragment=fragment,
                offset=original_pos,
                page=page,
            )
    return None


def _approx_original_offset(original: str, normalized: str, normalized_pos: int) -> int:
    if len(original) == len(normalized):
        return normalized_pos
    return min(len(original), normalized_pos)


def _stack_evidence_variants(term: str) -> list[str]:
    normalized = term.casefold().replace("ё", "е")
    variants = {
        "PostgreSQL": ["postgresql", "postgres"],
        "TIA Portal": ["tia portal", "portal 13", "portal 15", "portal 16"],
        "CoDeSys": ["codesys"],
        "SCADA": ["scada", "wincc", "movicon"],
        "АСКУЭ": ["аскуэ"],
        "электроучет": ["учет электроэнерг", "точк учет"],
        "REST API": ["rest api"],
        "API": ["api"],
        "1С": ["1с"],
    }
    for key, value in variants.items():
        if normalized == key.casefold().replace("ё", "е"):
            return value
    return [term]


def _requirement_evidence_variants(term: str) -> list[str]:
    normalized = term.casefold().replace("ё", "е")
    if normalized == "сро":
        return ["членство в сро", "наличие сро", "свидетельство сро", "саморегулируем"]
    if normalized == "допуск":
        return ["допуск"]
    if "backend (python)" in normalized:
        return ["python"]
    if "frontend (react)" in normalized:
        return ["react"]
    if "mobile (flutter)" in normalized:
        return ["flutter"]
    if normalized == "qa":
        return ["qa", "тестировщик", "тестирование"]
    return [term]


def _dedupe_texts(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _has_existing_system_experience_requirement(lower_text: str, projects: list[str]) -> bool:
    if not projects:
        return False
    markers = (
        "модификац",
        "доработ",
        "развит",
        "существующ",
        "эксплуатиру",
        "сопровожда",
    )
    return any(marker in lower_text for marker in markers)


def _extract_required_roles(lower_text: str, team: list[str]) -> list[str]:
    role_patterns = [
        (r"\bpython\b", "backend (Python)"),
        (r"\breact\b", "frontend (React)"),
        (r"\bflutter\b", "mobile (Flutter)"),
        (r"\bqa\b|тестировщик|инженер[а-я ]{0,30}тестирован", "QA"),
        (r"devops", "DevOps"),
        (r"аналитик|ba\b|бизнес-аналит", "аналитик/BA"),
        (r"архитектор", "архитектор"),
        (r"тимлид|team lead", "тимлид"),
        (r"электротехническ[а-я ]{0,20}лаборатор", "электротехническая лаборатория"),
        (r"аттестован[а-я ]{0,20}персонал", "аттестованный персонал"),
    ]
    found: list[str] = []
    source = " ".join(team) + " " + _role_requirement_windows(lower_text)
    for pattern, label in role_patterns:
        if re.search(pattern, source) and label not in found:
            found.append(label)
    return found


def _role_requirement_windows(lower_text: str) -> str:
    markers = (
        "роль специалист",
        "состав команды",
        "требования к команде",
        "команда исполнителя",
        "наличие специалист",
        "штатн",
        "специалист",
        "разработчик",
        "тестировщик",
        "qa",
        "devops",
        "аналитик",
        "архитектор",
        "тимлид",
    )
    windows: list[str] = []
    for marker in markers:
        start = 0
        while True:
            idx = lower_text.find(marker, start)
            if idx < 0:
                break
            windows.append(lower_text[max(0, idx - 160) : idx + 260])
            start = idx + len(marker)
    return " ".join(windows)


def _extract_other_requirement_signals(lower_text: str) -> list[str]:
    candidates = [
        ("референс", "референс-лист"),
        ("кадров", "документы о кадровых ресурсах"),
        ("квалификац", "квалификационные документы"),
        ("финансов", "финансовые документы"),
        ("письменно подтвердить соответствие", "письменное подтверждение соответствия требованиям"),
    ]
    found: list[str] = []
    for marker, label in candidates:
        if marker in lower_text and label not in found:
            found.append(label)
    return found


def has_participant_requirements(text: str) -> bool:
    if any(
        token in text
        for token in [
            "опыт участник",
            "требован к участник",
            "требования к поставщику",
            "требования к исполнителю",
            "требования к подрядчику",
            "квалификационные требования",
            "квалификац",
            "наличие опыта",
            "штат",
            "лиценз",
        ]
    ):
        return True
    return _has_explicit_sro_requirement(text)


def detect_procurement_type(text: str, policy: TriagePolicy | None = None) -> str:
    policy = policy or load_triage_policy()
    scores = {
        rule.key: count_hits(text, list(rule.tokens))
        for rule in policy.procurement_types
    }
    if not scores:
        return "не удалось определить"
    best_rule = max(policy.procurement_types, key=lambda rule: scores.get(rule.key, 0))
    if scores.get(best_rule.key, 0) == 0:
        return "не удалось определить"
    return best_rule.label


def extract_stack_signals(text: str) -> list[str]:
    regex_tokens = [
        (r"\bpython\b", "Python"),
        (r"\breact\b", "React"),
        (r"\bflutter\b", "Flutter"),
        (r"\bjava\b(?!\s*script)", "Java"),
        (r"\bkotlin\b", "Kotlin"),
        (r"\bswift\b", "Swift"),
        (r"\bphp\b", "PHP"),
        (r"\b1с\b", "1С"),
        (r"\bpostgresql\b", "PostgreSQL"),
        (r"\bpostgres\b", "PostgreSQL"),
        (r"\bmysql\b", "MySQL"),
        (r"\boracle\b", "Oracle"),
        (r"\btia\s*portal\b", "TIA Portal"),
        (r"\bportal\s*13\b", "TIA Portal"),
        (r"\bcodesys\b", "CoDeSys"),
        (r"\bwincc\b", "WinCC"),
        (r"\bmovicon\b", "Movicon"),
        (r"\bscada\b", "SCADA"),
        (r"\bplc\b", "PLC"),
        (r"\bаску[эe]\b", "АСКУЭ"),
        (r"автоматизированн[а-я ]{0,40}систем[а-я ]{0,20}(контрол[яь]|учет)[а-я ]{0,30}", "АСКУЭ"),
        (r"точк[аи] учет", "электроучет"),
        (r"учет[а-я ]{0,20}электроэнерг", "электроучет"),
        (r"электротехническ[а-я ]{0,30}лаборатор", "промышленная автоматизация"),
        (r"\bпнр\b", "промышленная автоматизация"),
        (r"пусконалад", "промышленная автоматизация"),
        (r"\bсмр\b", "промышленная автоматизация"),
        (r"\biec\s*61131-3\b", "IEC 61131-3"),
        (r"\blad\b", "LAD"),
        (r"\bscl\b", "SCL"),
        (r"\bld\b", "LD"),
        (r"\bst\b", "ST"),
        (r"\bangular\b", "Angular"),
        (r"\bvue\b", "Vue"),
        (r"\bruby on rails\b", "Ruby on Rails"),
        (r"\brails\b", "Ruby on Rails"),
        (r"\bdocker\b", "Docker"),
        (r"\bkubernetes\b", "Kubernetes"),
        (r"\brest api\b", "REST API"),
        (r"\bapi\b", "API"),
        (r"\bios\b", "iOS"),
        (r"\bandroid\b", "Android"),
    ]
    found: list[str] = []
    for pattern, label in regex_tokens:
        if label == "1С" and not _has_1c_stack_context(text):
            continue
        if label == "API" and not _has_api_stack_context(text):
            continue
        if label in {"PLC", "LD", "ST"} and not _has_industrial_stack_context(text):
            continue
        if re.search(pattern, text) and label not in found:
            found.append(label)
    return found


def _has_industrial_stack_context(text: str) -> bool:
    markers = (
        "scada",
        "асутп",
        "плк",
        "plc",
        "tia portal",
        "codesys",
        "wincc",
        "movicon",
        "iec 61131",
        "мэк 61131",
        "lad",
        "scl",
        "пусконалад",
        "контроллер",
    )
    return any(marker in text for marker in markers)


def _has_1c_stack_context(text: str) -> bool:
    integration_markers = (
        "интеграц",
        "обмен данн",
        "api",
        "интерфейс",
        "синхрон",
        "шина",
        "сервис",
    )
    dev_markers = (
        "разработ",
        "конфигурац",
        "платформ",
        "предприят",
        "доработ",
        "модификац",
    )
    for match in re.finditer(r"\b1с\b", text):
        start = max(0, match.start() - 80)
        end = min(len(text), match.end() + 80)
        window = text[start:end]
        if any(marker in window for marker in dev_markers):
            return True
        if any(marker in window for marker in integration_markers):
            continue
    return False


def _has_api_stack_context(text: str) -> bool:
    definition_markers = (
        "интерфейс программирования приложений",
        "определения",
        "сокращения",
        "перечень основных понятий",
    )
    usage_markers = (
        "rest",
        "graphql",
        "endpoint",
        "эндпоинт",
        "интеграц",
        "взаимодейств",
        "вызов",
        "доступ",
        "публикац",
        "сервис",
        "микросервис",
        "swagger",
        "openapi",
        "документац api",
    )
    for match in re.finditer(r"\bapi\b", text):
        start = max(0, match.start() - 120)
        end = min(len(text), match.end() + 120)
        window = text[start:end]
        if any(marker in window for marker in definition_markers):
            continue
        if any(marker in window for marker in usage_markers):
            return True
    return False


def _normalize_team_requirements(requirements: list[str]) -> list[str]:
    normalized: list[str] = []
    for item in requirements:
        fragment = item
        if re.search(r"кадров[а-я ]{0,20}ресурс", fragment, re.I):
            fragment = "документы о кадровых ресурсах"
        if fragment not in normalized:
            normalized.append(fragment)
    return normalized


def extract_numeric_requirements(text: str, patterns: list[re.Pattern[str]]) -> list[str]:
    found: list[str] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            fragment = re.sub(r"\s+", " ", match.group(0)).strip(" ,;.")
            if len(fragment) < 6:
                continue
            if _is_duplicate_or_less_specific(fragment, found):
                continue
            found = [item for item in found if not _is_less_specific_requirement(item, fragment)]
            if fragment not in found:
                found.append(fragment)
    return found


def _is_duplicate_or_less_specific(fragment: str, existing: list[str]) -> bool:
    return any(fragment == item or _is_less_specific_requirement(fragment, item) for item in existing)


def _is_less_specific_requirement(candidate: str, reference: str) -> bool:
    candidate_key = ru_normalize(candidate)
    reference_key = ru_normalize(reference)
    return candidate_key in reference_key and len(candidate_key) < len(reference_key)


def extract_license_signals(text: str) -> list[str]:
    found: list[str] = []
    credential_checks = [
        (r"образовательн[а-я ]{0,20}лиценз", "образовательная лицензия"),
        (r"лиценз", "лицензия"),
        (r"аккредит", "аккредитация"),
        (r"сертификат", "сертификаты"),
        (r"iso\s*27001", "ISO 27001"),
        (r"iso\s*9001", "ISO 9001"),
        (r"iso\s*20000", "ISO 20000"),
        (r"\bфстэк\b", "ФСТЭК"),
        (r"\bфсб\b", "ФСБ"),
    ]
    for pattern, label in credential_checks:
        if _has_explicit_credential_requirement(text, pattern) and label not in found:
            found.append(label)
    if _has_explicit_sro_requirement(text) and "СРО" not in found:
        found.append("СРО")
    if _has_explicit_access_requirement(text) and "допуск" not in found:
        found.append("допуск")
    return found


def _has_explicit_sro_requirement(text: str) -> bool:
    sro_patterns = [
        r"членств[ао]\s+в\s+\bсро\b",
        r"наличи[ея]\s+\bсро\b",
        r"свидетельств[ао]\s+\bсро\b",
        r"выписк[аи]\s+из\s+реестр[а-я ]{0,20}\bсро\b",
        r"допуск[а-я ]{0,20}\bсро\b",
        r"саморегулируем[а-я ]{0,30}организац",
        r"участник[а-я ]{0,60}\bсро\b",
        r"исполнитель[а-я ]{0,60}\bсро\b",
        r"подрядчик[а-я ]{0,60}\bсро\b",
        r"требован[а-я ]{0,40}\bсро\b",
    ]
    return any(re.search(pattern, text) for pattern in sro_patterns)


def _has_explicit_access_requirement(text: str) -> bool:
    access_patterns = [
        r"(требуетс[я]|необходим[аоы]|наличи[ея]|обязан[а-я ]{0,20}иметь|должен[а-я ]{0,20}иметь)[а-я ]{0,40}допуск",
        r"допуск[а-я ]{0,40}(к гостайн|к государственной тайне|к работам|на объект|к объекту)",
    ]
    return any(re.search(pattern, text) for pattern in access_patterns)


def _has_explicit_credential_requirement(text: str, keyword_pattern: str) -> bool:
    requirement_markers = (
        "требуетс",
        "необходим",
        "наличие",
        "обязан",
        "должен",
        "иметь",
        "предостав",
        "подтверд",
        "требован",
    )
    negative_markers = (
        "лицензионн соглаш",
        "лицензионный договор",
        "лицензионное соглашение",
        "условия лицензирования по",
        "право использования по",
    )
    subject_markers = (
        "участник",
        "исполнитель",
        "подрядчик",
        "поставщик",
        "организац",
        "компан",
    )
    explicit_patterns = [
        rf"(требуетс[я]|необходим[аоы]|наличи[ея]|обязан[а-я ]{{0,20}}иметь|должен[а-я ]{{0,20}}иметь)[а-я ]{{0,60}}{keyword_pattern}",
        rf"{keyword_pattern}[а-я ]{{0,60}}(участник|исполнитель|подрядчик|поставщик)",
    ]
    if any(re.search(pattern, text) for pattern in explicit_patterns):
        return True
    for match in re.finditer(keyword_pattern, text):
        start = max(0, match.start() - 120)
        end = min(len(text), match.end() + 120)
        window = text[start:end]
        if any(marker in window for marker in negative_markers):
            continue
        if any(marker in window for marker in requirement_markers) and any(marker in window for marker in subject_markers):
            return True
    return False


def extract_payment_signals(text: str) -> list[str]:
    patterns = [
        r"постоплат[аы][^.\n]{0,80}",
        r"предоплат[аы][^.\n]{0,80}",
        r"оплат[аы][^.\n]{0,120}рабоч",
        r"в течени[е][^.\n]{0,60}\d+\s*(календарн|рабоч)[^.\n]{0,20}дн",
        r"по факту выполнени[яа][^.\n]{0,100}",
        r"авансировани[а-я ]{0,20}не предусмотрен[ао]",
        r"банковск[а-я ]{0,20}гарант[а-я ]{0,80}",
        r"обеспечени[а-я ]{0,40}(заявк|договор)",
    ]
    found: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            fragment = re.sub(r"\s+", " ", match.group(0)).strip(" ,;.")
            if fragment and fragment not in found:
                found.append(fragment)
    return found


def count_hits(text: str, needles: list[str]) -> int:
    return sum(1 for needle in needles if _contains_policy_token(text, needle))


def _contains_policy_token(text: str, needle: str) -> bool:
    normalized = ru_normalize(needle)
    if not normalized:
        return False
    if len(normalized) <= 3 and re.fullmatch(r"[a-zа-я0-9]+", normalized):
        return re.search(rf"(?<![a-zа-я0-9]){re.escape(normalized)}(?![a-zа-я0-9])", text) is not None
    return normalized in text


def _infer_page_from_text_offset(text: str, offset: int) -> int | None:
    prefix = text[: max(0, offset)]
    matches = list(re.finditer(r"\[page\s+(\d+)\]", prefix, flags=re.I))
    if not matches:
        return None
    try:
        return int(matches[-1].group(1))
    except ValueError:
        return None


def ru_normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold().replace("ё", "е"))


TURNOVER_PATTERNS = [
    re.compile(r"(оборот|выручк)[^.\n]{0,60}?(не менее|не ниж[е]|от)\s*\d[\d\s.,]*\s*(млн|миллион|руб)", re.I),
    re.compile(r"(финансов[а-я ]{0,20}устойчивост[а-я ]{0,20}|годов[а-я ]{0,20}выручк[а-я ]{0,20})[^.\n]{0,80}", re.I),
]

PROJECT_PATTERNS = [
    re.compile(r"(не менее|минимум|от)\s*\d+\s*(аналогичн|реализованн|исполненн)[^.\n]{0,80}(проект|контракт|договор)", re.I),
    re.compile(r"опыт проведения исследований в категории[^.\n]{0,160}?(?= участники закупки|$)", re.I),
    re.compile(
        r"(опыт|наличие опыта)[^.\n]{0,120}"
        r"(мобил|медиа|\bai\b|\bии\b|искусственн[а-я ]{0,20}интеллект|веб|портал|сайт|платформ|геосервис|бронирован)",
        re.I,
    ),
    re.compile(r"\d+\s*(или|и)\s*более[^.\n]{0,40}(договор|контракт|проект)", re.I),
    re.compile(r"референс[- ]?лист[^.\n]{0,80}", re.I),
]

TEAM_PATTERNS = [
    re.compile(r"(штат|численност[ья]|сотрудник|специалист)[^.\n]{0,60}(не менее|от)\s*\d+", re.I),
    re.compile(r"(команд[аы]|штатн[а-я ]{0,20}специалист)[^.\n]{0,80}", re.I),
    re.compile(r"наличие персонала[^.\n]{0,120}", re.I),
    re.compile(r"аттестованн[а-я ]{0,40}персонал[^.\n]{0,80}", re.I),
    re.compile(r"электротехническ[а-я ]{0,30}лаборатор[а-я ]{0,80}", re.I),
    re.compile(r"кадров[а-я ]{0,20}ресурс[а-я ]{0,40}", re.I),
    re.compile(r"наличие сертифицированн[а-я ]{0,40}специалист", re.I),
]
