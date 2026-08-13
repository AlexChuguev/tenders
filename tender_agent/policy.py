from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha1
from pathlib import Path


@dataclass(frozen=True)
class ProcurementTypeRule:
    key: str
    label: str
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class PriorityHintPolicy:
    site_budget_threshold_rub: int
    site_tokens: tuple[str, ...]
    strong_product_tokens: tuple[str, ...]
    low_priority_message: str


@dataclass(frozen=True)
class ConfidencePolicy:
    base_score: int
    procurement_type_bonus: int
    tz_bonus: int
    notice_bonus: int
    participant_requirements_bonus: int
    contract_or_payment_bonus: int
    stack_bonus: int
    completeness_high_bonus: int
    completeness_low_penalty: int
    triage_bonus: int
    min_score: int
    max_score: int
    llm_blend: bool
    non_profile_floor: int
    construction_floor: int
    outstaffing_floor: int
    legacy_modification_floor: int
    low_priority_cap: int
    infosec_floor: int
    enterprise_floor: int
    strong_stack_floor: int


@dataclass(frozen=True)
class TriagePolicy:
    source_path: str
    content_sha1: str
    procurement_types: tuple[ProcurementTypeRule, ...]
    non_profile_procurement_types: tuple[str, ...]
    priority_hint: PriorityHintPolicy
    enterprise_hard_tokens: tuple[str, ...]
    enterprise_soft_tokens: tuple[str, ...]
    development_context_tokens: tuple[str, ...]
    infosec_license_tokens: tuple[str, ...]
    infosec_hard_tokens: tuple[str, ...]
    infosec_medium_tokens: tuple[str, ...]
    legacy_modification_tokens: tuple[str, ...]
    legacy_system_tokens: tuple[str, ...]
    construction_tokens: tuple[str, ...]
    outstaffing_tokens: tuple[str, ...]
    core_stack: tuple[str, ...]
    non_core_stack_tokens: tuple[str, ...]
    confidence: ConfidencePolicy


DEFAULT_POLICY_PATH = Path(__file__).resolve().parent.parent / "triage_policy.json"


def load_triage_policy(path: Path | None = None) -> TriagePolicy:
    resolved = (path or DEFAULT_POLICY_PATH).expanduser().resolve()
    return _load_triage_policy_cached(str(resolved))


@lru_cache(maxsize=8)
def _load_triage_policy_cached(path_str: str) -> TriagePolicy:
    path = Path(path_str)
    raw_text = path.read_text(encoding="utf-8")
    payload = json.loads(raw_text)
    return TriagePolicy(
        source_path=str(path),
        content_sha1=sha1(raw_text.encode("utf-8")).hexdigest(),
        procurement_types=tuple(
            ProcurementTypeRule(
                key=str(item["key"]),
                label=str(item["label"]),
                tokens=tuple(str(token) for token in item.get("tokens", [])),
            )
            for item in payload.get("procurement_types", [])
        ),
        non_profile_procurement_types=tuple(str(item) for item in payload.get("non_profile_procurement_types", [])),
        priority_hint=_load_priority_hint(payload.get("priority_hint", {})),
        enterprise_hard_tokens=tuple(str(item) for item in payload.get("enterprise_hard_tokens", [])),
        enterprise_soft_tokens=tuple(str(item) for item in payload.get("enterprise_soft_tokens", [])),
        development_context_tokens=tuple(str(item) for item in payload.get("development_context_tokens", [])),
        infosec_license_tokens=tuple(str(item) for item in payload.get("infosec_license_tokens", [])),
        infosec_hard_tokens=tuple(str(item) for item in payload.get("infosec_hard_tokens", [])),
        infosec_medium_tokens=tuple(str(item) for item in payload.get("infosec_medium_tokens", [])),
        legacy_modification_tokens=tuple(str(item) for item in payload.get("legacy_modification_tokens", [])),
        legacy_system_tokens=tuple(str(item) for item in payload.get("legacy_system_tokens", [])),
        construction_tokens=tuple(str(item) for item in payload.get("construction_tokens", [])),
        outstaffing_tokens=tuple(str(item) for item in payload.get("outstaffing_tokens", [])),
        core_stack=tuple(str(item) for item in payload.get("core_stack", [])),
        non_core_stack_tokens=tuple(str(item) for item in payload.get("non_core_stack_tokens", [])),
        confidence=_load_confidence(payload.get("confidence", {})),
    )


def policy_metadata(policy: TriagePolicy) -> dict[str, str]:
    return {
        "path": policy.source_path,
        "sha1": policy.content_sha1,
    }


def _load_priority_hint(payload: dict) -> PriorityHintPolicy:
    return PriorityHintPolicy(
        site_budget_threshold_rub=int(payload.get("site_budget_threshold_rub", 2_000_000)),
        site_tokens=tuple(str(item) for item in payload.get("site_tokens", [])),
        strong_product_tokens=tuple(str(item) for item in payload.get("strong_product_tokens", [])),
        low_priority_message=str(
            payload.get(
                "low_priority_message",
                "низкий приоритет: тендер на сайт / веб-тематику с бюджетом ниже 2 млн руб.; "
                "не повышай решение только из-за web-формулировок",
            )
        ),
    )


def _load_confidence(payload: dict) -> ConfidencePolicy:
    return ConfidencePolicy(
        base_score=int(payload.get("base_score", 35)),
        procurement_type_bonus=int(payload.get("procurement_type_bonus", 10)),
        tz_bonus=int(payload.get("tz_bonus", 15)),
        notice_bonus=int(payload.get("notice_bonus", 10)),
        participant_requirements_bonus=int(payload.get("participant_requirements_bonus", 10)),
        contract_or_payment_bonus=int(payload.get("contract_or_payment_bonus", 10)),
        stack_bonus=int(payload.get("stack_bonus", 5)),
        completeness_high_bonus=int(payload.get("completeness_high_bonus", 10)),
        completeness_low_penalty=int(payload.get("completeness_low_penalty", 10)),
        triage_bonus=int(payload.get("triage_bonus", 10)),
        min_score=int(payload.get("min_score", 5)),
        max_score=int(payload.get("max_score", 100)),
        llm_blend=bool(payload.get("llm_blend", True)),
        non_profile_floor=int(payload.get("non_profile_floor", 90)),
        construction_floor=int(payload.get("construction_floor", 92)),
        outstaffing_floor=int(payload.get("outstaffing_floor", 90)),
        legacy_modification_floor=int(payload.get("legacy_modification_floor", 85)),
        low_priority_cap=int(payload.get("low_priority_cap", 60)),
        infosec_floor=int(payload.get("infosec_floor", 97)),
        enterprise_floor=int(payload.get("enterprise_floor", 92)),
        strong_stack_floor=int(payload.get("strong_stack_floor", 75)),
    )
