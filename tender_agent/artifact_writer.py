from __future__ import annotations

import json
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime
from hashlib import sha1
from pathlib import Path
from typing import Any

from tender_agent.analysis_types import AnalysisPayload
from tender_agent.models import TenderAnalysisResult
from tender_agent.versioning import ARTIFACT_SCHEMA_VERSION, build_versions_payload


class ArtifactWriter:
    def __init__(
        self,
        root: Path,
        source: str,
        batch_name: str,
        policy_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.root = root
        self.source = _sanitize_segment(source) or "unknown"
        self.batch_name = _sanitize_segment(batch_name) or "batch"
        self.policy_metadata = _to_jsonable(policy_metadata or {})
        self.batch_dir = self.root / self.source / self.batch_name
        self.batch_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.batch_dir / "_index.jsonl"

    def write(
        self,
        result: TenderAnalysisResult,
        analysis: AnalysisPayload | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Path:
        if analysis is not None and analysis.facts is not None and not result.reason_codes:
            result.reason_codes = list(analysis.facts.reason_codes)
        file_name = _artifact_file_name(result)
        file_path = self.batch_dir / file_name
        if self._should_preserve_existing(file_path, result):
            return file_path

        extra_payload = dict(extra or {})
        if analysis is not None and analysis.facts is not None and "facts" not in extra_payload:
            extra_payload["facts"] = analysis.facts

        payload = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "artifact_type": "tender_analysis",
            "written_at": datetime.now().isoformat(timespec="seconds"),
            "source": self.source,
            "batch_name": self.batch_name,
            "batch_path": str(self.batch_dir),
            "policy": self.policy_metadata or None,
            "versions": build_versions_payload(policy_sha1=str((self.policy_metadata or {}).get("sha1") or "")),
            "result": _to_jsonable(result),
            "analysis": _to_jsonable(analysis) if analysis is not None else None,
            "extra": _to_jsonable(extra_payload),
        }
        file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _append_index_entry(self.index_path, file_name=file_name, title=result.title, url=result.url)
        return file_path

    def _should_preserve_existing(self, file_path: Path, incoming: TenderAnalysisResult) -> bool:
        if not file_path.exists():
            return False
        if incoming.error == "network_error":
            return True
        if not _is_technical_retry_result(incoming):
            return False
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        existing_result = payload.get("result") or {}
        existing = TenderAnalysisResult(
            tender_id=str(existing_result.get("tender_id") or ""),
            title=str(existing_result.get("title") or ""),
            url=str(existing_result.get("url") or ""),
            deadline_at=None,
            customer=str(existing_result.get("customer") or ""),
            customer_inn=str(existing_result.get("customer_inn") or ""),
            decision=str(existing_result.get("decision") or "Уточнить"),
            confidence_percent=_coerce_int(existing_result.get("confidence_percent")),
            summary_text=str(existing_result.get("summary_text") or ""),
            downloaded_files=[str(item) for item in (existing_result.get("downloaded_files") or [])],
            analysis_markdown=str(existing_result.get("analysis_markdown") or ""),
            nmck_rub=None,
            document_completeness=str(existing_result.get("document_completeness") or ""),
            error=str(existing_result.get("error") or ""),
            seldon_added_at=None,
            batch_name=str(existing_result.get("batch_name") or payload.get("batch_name") or ""),
            batch_path=str(existing_result.get("batch_path") or payload.get("batch_path") or ""),
            facts_stack=str(existing_result.get("facts_stack") or ""),
            facts_requirements=str(existing_result.get("facts_requirements") or ""),
            facts_docs=str(existing_result.get("facts_docs") or ""),
            facts_payment=str(existing_result.get("facts_payment") or ""),
            facts_req_sro=str(existing_result.get("facts_req_sro") or ""),
            facts_req_turnover=str(existing_result.get("facts_req_turnover") or ""),
            facts_req_analog_projects=str(existing_result.get("facts_req_analog_projects") or ""),
            facts_req_legacy_experience=str(existing_result.get("facts_req_legacy_experience") or ""),
            facts_req_roles=str(existing_result.get("facts_req_roles") or ""),
            facts_req_licenses=str(existing_result.get("facts_req_licenses") or ""),
            reason_codes=[str(item) for item in (existing_result.get("reason_codes") or [])],
        )
        return not _is_technical_retry_result(existing)


def _artifact_file_name(result: TenderAnalysisResult) -> str:
    slug = _sanitize_segment(result.title) or "tender"
    suffix_source = result.tender_id.strip() or result.url.strip() or result.title
    digest = sha1(suffix_source.encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"{slug}__{digest}.json"


def _sanitize_segment(value: str) -> str:
    normalized = re.sub(r"\s+", "_", value.strip())
    normalized = re.sub(r"[^0-9A-Za-zА-Яа-яЁё._-]+", "_", normalized)
    normalized = normalized.strip("._-")
    return normalized[:120]


def _to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {key: _to_jsonable(val) for key, val in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _to_jsonable(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(item) for item in value]
    return value


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _is_technical_retry_result(result: TenderAnalysisResult) -> bool:
    try:
        confidence = int(result.confidence_percent or 0)
    except Exception:
        confidence = 0
    if confidence != 0:
        return False
    summary = result.summary_text.lower()
    return (
        "требуется повторный прогон анализа" in summary
        or "технической ошибки анализа" in summary
        or "не удалось получить ответ модели" in summary
        or "dns resolution failed" in summary
        or "автоматически не определ" in summary
    )


def _append_index_entry(index_path: Path, *, file_name: str, title: str, url: str) -> None:
    entry = {"file": file_name, "title": title, "url": url}
    existing_lines: list[str] = []
    if index_path.exists():
        try:
            existing_lines = index_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            existing_lines = []
    filtered: list[str] = []
    for line in existing_lines:
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if str(payload.get("file") or "") == file_name:
            continue
        filtered.append(json.dumps(payload, ensure_ascii=False))
    filtered.append(json.dumps(entry, ensure_ascii=False))
    index_path.write_text("\n".join(filtered) + "\n", encoding="utf-8")
