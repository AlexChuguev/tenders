from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tender_agent.analysis_types import LLMFinding


ALLOWED_CATEGORIES = {"risks", "stack", "requirements", "docs", "payment"}


def parse_llm_findings(value: Any) -> list[LLMFinding]:
    if not isinstance(value, list):
        return []
    findings: list[LLMFinding] = []
    for row in value:
        if not isinstance(row, dict):
            continue
        category = str(row.get("category") or "").strip().casefold()
        if category not in ALLOWED_CATEGORIES:
            continue
        text = _clean_text(str(row.get("text") or ""))
        quote = _clean_text(str(row.get("quote") or ""))
        if not text or not quote:
            continue
        findings.append(
            LLMFinding(
                category=category,
                text=text,
                quote=quote,
                source_file=str(row.get("source_file") or "").strip(),
            )
        )
    return findings[:15]


def verify_llm_findings(findings: list[LLMFinding], prepared_files: list[Path]) -> list[LLMFinding]:
    if not findings:
        return []
    corpus = _load_corpus(prepared_files)
    verified: list[LLMFinding] = []
    for finding in findings:
        quote_norm = _normalize(finding.quote)
        if len(quote_norm) < 12:
            verified.append(_replace_finding(finding, verified=False, reason="quote_too_short"))
            continue
        matched_file = _find_quote_file(quote_norm, corpus)
        if matched_file:
            verified.append(
                _replace_finding(
                    finding,
                    verified=True,
                    reason="quote_found",
                    source_file=finding.source_file or matched_file,
                )
            )
            continue
        verified.append(_replace_finding(finding, verified=False, reason="quote_not_found"))
    return verified


def merge_verified_findings_into_summary(summary_points: list[str], findings: list[LLMFinding]) -> list[str]:
    verified = [item for item in findings if item.verified and item.text.strip()]
    if not verified:
        return summary_points
    by_category: dict[str, list[str]] = {}
    for item in verified:
        by_category.setdefault(item.category, [])
        if item.text not in by_category[item.category]:
            by_category[item.category].append(item.text)

    out: list[str] = []
    for line in summary_points:
        category = _line_category(line)
        replacements = by_category.get(category) or []
        if replacements and _should_replace_with_verified(line, replacements):
            prefix = line.split(":", 1)[0] if ":" in line else ""
            text = "; ".join(replacements[:3])
            out.append(f"{prefix}: {text}" if prefix else text)
            continue
        out.append(line)
    return out


def _line_category(line: str) -> str:
    lowered = line.casefold().replace("ё", "е")
    if "стек:" in lowered:
        return "stack"
    if "требования к контрагенту:" in lowered or "требования к участнику:" in lowered:
        return "requirements"
    if "документация:" in lowered:
        return "docs"
    if "оплата" in lowered:
        return "payment"
    if "риски:" in lowered:
        return "risks"
    return ""


def _should_replace_with_verified(line: str, replacements: list[str]) -> bool:
    lowered = line.casefold().replace("ё", "е")
    if any(token in lowered for token in ("не указано", "не извлеч", "требует проверки", "не раскрыт")):
        return True
    return len("; ".join(replacements)) > len(line.split(":", 1)[-1].strip()) + 10


def _load_corpus(files: list[Path]) -> dict[str, str]:
    corpus: dict[str, str] = {}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            text = ""
        if text.strip():
            corpus[path.name] = _normalize(text)
    return corpus


def _find_quote_file(quote_norm: str, corpus: dict[str, str]) -> str:
    for name, text in corpus.items():
        if quote_norm in text:
            return name
    compact_quote = re.sub(r"\s+", " ", quote_norm).strip()
    for name, text in corpus.items():
        if compact_quote and compact_quote in re.sub(r"\s+", " ", text):
            return name
    return ""


def _replace_finding(
    finding: LLMFinding,
    *,
    verified: bool,
    reason: str,
    source_file: str | None = None,
) -> LLMFinding:
    return LLMFinding(
        category=finding.category,
        text=finding.text,
        quote=finding.quote,
        source_file=source_file if source_file is not None else finding.source_file,
        verified=verified,
        reason=reason,
    )


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold().replace("ё", "е")).strip()


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t\r\n;,.")
