from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SearchProfile:
    name: str
    include_any: list[str]
    exclude_any: list[str]
    secondary_signals: list[str]
    require_secondary: bool

    @classmethod
    def load(cls, path: Path) -> "SearchProfile":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            name=str(payload.get("name", "default")).strip(),
            include_any=[str(item).strip() for item in payload.get("include_any", []) if str(item).strip()],
            exclude_any=[str(item).strip() for item in payload.get("exclude_any", []) if str(item).strip()],
            secondary_signals=[str(item).strip() for item in payload.get("secondary_signals", []) if str(item).strip()],
            require_secondary=bool(payload.get("require_secondary", False)),
        )


@dataclass(frozen=True)
class SearchProfileMatch:
    include_hits: list[str]
    exclude_hits: list[str]
    secondary_hits: list[str]
    require_secondary: bool

    @property
    def is_candidate(self) -> bool:
        if not self.include_hits or self.exclude_hits:
            return False
        if self.require_secondary and not self.secondary_hits:
            return False
        return True


def evaluate_text(profile: SearchProfile, text: str) -> SearchProfileMatch:
    normalized = _normalize_text(text)
    include_hits = [term for term in profile.include_any if _matches(term, normalized)]
    exclude_hits = [term for term in profile.exclude_any if _matches(term, normalized)]
    secondary_hits = [term for term in profile.secondary_signals if _matches(term, normalized)]
    return SearchProfileMatch(
        include_hits=include_hits,
        exclude_hits=exclude_hits,
        secondary_hits=secondary_hits,
        require_secondary=profile.require_secondary,
    )


def _normalize_text(value: str) -> str:
    return (
        value.lower()
        .replace("ё", "е")
        .replace("̆", "")
    )


def _matches(pattern: str, normalized_text: str) -> bool:
    raw = pattern.strip().strip('"')
    escaped = re.escape(_normalize_text(raw))
    escaped = escaped.replace(r"\*", r".*")
    regex = re.compile(escaped)
    return bool(regex.search(normalized_text))
