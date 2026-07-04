from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from tender_agent.models import TenderAnalysisResult


@dataclass(frozen=True)
class StoredArtifact:
    path: Path
    schema_version: int
    written_at: datetime | None
    source: str
    batch_name: str
    policy: dict[str, Any]
    versions: dict[str, Any]
    result: TenderAnalysisResult
    analysis: dict[str, Any] | None
    extra: dict[str, Any]


def iter_artifacts(
    root: Path,
    *,
    source: str | None = None,
    batch_name: str | None = None,
    policy_sha1: str | None = None,
) -> list[StoredArtifact]:
    if not root.exists():
        return []
    artifacts: list[StoredArtifact] = []
    for path in sorted(root.rglob("*.json")):
        if path.name.startswith("_"):
            continue
        artifact = load_artifact(path)
        if source and artifact.source != source:
            continue
        if batch_name and artifact.batch_name != batch_name:
            continue
        if policy_sha1 and str(artifact.policy.get("sha1") or "") != policy_sha1:
            continue
        artifacts.append(artifact)
    artifacts.sort(key=_artifact_sort_key)
    return artifacts


def load_artifact(path: Path) -> StoredArtifact:
    payload = json.loads(path.read_text(encoding="utf-8"))
    result_payload = payload.get("result") or {}
    result = _load_result(result_payload)
    batch_name = str(payload.get("batch_name") or "")
    if batch_name and not result.batch_name:
        result.batch_name = batch_name
    if batch_name and not result.batch_path:
        result.batch_path = str(payload.get("batch_path") or "")
    return StoredArtifact(
        path=path,
        schema_version=_parse_int(payload.get("schema_version"), default=0),
        written_at=_parse_datetime(payload.get("written_at")),
        source=str(payload.get("source") or ""),
        batch_name=batch_name,
        policy=_load_mapping(payload.get("policy")),
        versions=_load_mapping(payload.get("versions")),
        result=result,
        analysis=_load_mapping(payload.get("analysis")),
        extra=_load_mapping(payload.get("extra")),
    )


def unique_latest_results(artifacts: Iterable[StoredArtifact]) -> list[TenderAnalysisResult]:
    return [artifact.result for artifact in unique_latest_artifacts(artifacts)]


def unique_latest_artifacts(artifacts: Iterable[StoredArtifact]) -> list[StoredArtifact]:
    latest: dict[str, StoredArtifact] = {}
    for artifact in artifacts:
        key = _result_identity(artifact.result)
        current = latest.get(key)
        if current is None or _artifact_sort_key(artifact) >= _artifact_sort_key(current):
            latest[key] = artifact
    results = list(latest.values())
    results.sort(
        key=lambda artifact: (
            artifact.result.deadline_at is None,
            artifact.result.deadline_at or datetime.max,
            artifact.result.title.casefold(),
        )
    )
    return results


def latest_artifact_map(artifacts: Iterable[StoredArtifact]) -> dict[str, StoredArtifact]:
    return {_result_identity(artifact.result): artifact for artifact in unique_latest_artifacts(artifacts)}


def collect_policy_sha1s(artifacts: Iterable[StoredArtifact]) -> list[str]:
    values = {
        str(artifact.policy.get("sha1") or "").strip()
        for artifact in artifacts
        if str(artifact.policy.get("sha1") or "").strip()
    }
    return sorted(values)


def _load_result(payload: dict[str, Any]) -> TenderAnalysisResult:
    return TenderAnalysisResult(
        tender_id=str(payload.get("tender_id") or ""),
        title=str(payload.get("title") or ""),
        url=str(payload.get("url") or ""),
        deadline_at=_parse_datetime(payload.get("deadline_at")),
        customer=str(payload.get("customer") or ""),
        customer_inn=str(payload.get("customer_inn") or ""),
        decision=str(payload.get("decision") or "Уточнить"),
        confidence_percent=_parse_int(payload.get("confidence_percent"), default=0),
        summary_text=str(payload.get("summary_text") or ""),
        downloaded_files=[str(item) for item in (payload.get("downloaded_files") or [])],
        analysis_markdown=str(payload.get("analysis_markdown") or ""),
        nmck_rub=_parse_float(payload.get("nmck_rub")),
        document_completeness=str(payload.get("document_completeness") or ""),
        error=str(payload.get("error") or ""),
        seldon_added_at=_parse_datetime(payload.get("seldon_added_at")),
        batch_name=str(payload.get("batch_name") or ""),
        batch_path=str(payload.get("batch_path") or ""),
        facts_stack=str(payload.get("facts_stack") or ""),
        facts_requirements=str(payload.get("facts_requirements") or ""),
        facts_docs=str(payload.get("facts_docs") or ""),
        facts_payment=str(payload.get("facts_payment") or ""),
        facts_req_sro=str(payload.get("facts_req_sro") or ""),
        facts_req_turnover=str(payload.get("facts_req_turnover") or ""),
        facts_req_analog_projects=str(payload.get("facts_req_analog_projects") or ""),
        facts_req_legacy_experience=str(payload.get("facts_req_legacy_experience") or ""),
        facts_req_roles=str(payload.get("facts_req_roles") or ""),
        facts_req_licenses=str(payload.get("facts_req_licenses") or ""),
        reason_codes=[str(item) for item in (payload.get("reason_codes") or [])],
    )


def _load_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value))
    text = str(value).strip()
    if not text:
        return None
    for fmt in (
        None,
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
    ):
        try:
            if fmt is None:
                return datetime.fromisoformat(text)
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _result_identity(result: TenderAnalysisResult) -> str:
    url = result.url.strip()
    tender_id = result.tender_id.strip()
    title = result.title.strip().casefold()
    if url and tender_id:
        return f"url:{url}|id:{tender_id}"
    if result.url.strip():
        return f"url:{result.url.strip()}"
    if result.tender_id.strip():
        return f"id:{result.tender_id.strip()}"
    batch = result.batch_name.strip()
    return f"batch:{batch}|title:{title}"


def _artifact_sort_key(artifact: StoredArtifact) -> tuple[datetime, datetime, str]:
    return (
        artifact.result.deadline_at or datetime.max,
        artifact.written_at or datetime.min,
        artifact.path.name,
    )
