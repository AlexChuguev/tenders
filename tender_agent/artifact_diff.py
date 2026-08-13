from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from tender_agent.analysis_types import ExtractedFacts
from tender_agent.artifact_reader import StoredArtifact
from tender_agent.rerender import rerender_artifact_result


@dataclass(frozen=True)
class ArtifactRerenderDiff:
    title: str
    url: str
    artifact_path: str
    schema_version: int
    stored_versions: dict[str, Any]
    decision_before: str
    decision_after: str
    confidence_before: int
    confidence_after: int
    summary_changed: bool
    facts_stack_before: str
    facts_stack_after: str
    facts_requirements_before: str
    facts_requirements_after: str
    reason_codes_before: list[str]
    reason_codes_after: list[str]
    added_reason_codes: list[str]
    removed_reason_codes: list[str]
    triage_signals_before: list[str]
    triage_signals_after: list[str]
    added_signals: list[str]
    removed_signals: list[str]
    quality_flags_before: list[str]
    quality_flags_after: list[str]
    changed: bool


def diff_artifact_vs_rerender(artifact: StoredArtifact) -> ArtifactRerenderDiff | None:
    rerendered = rerender_artifact_result(artifact)
    if rerendered is None:
        return None
    payload, facts = rerendered
    facts_stack_after, facts_requirements_after, _, _ = _build_fact_strings(facts)
    reason_codes_before = list(artifact.result.reason_codes or [])
    reason_codes_after = list(facts.reason_codes or [])
    stored_facts = (artifact.analysis or {}).get("facts") or artifact.extra.get("facts") or {}
    triage_before = [str(item) for item in (stored_facts.get("triage_signals") or [])]
    quality_before = [str(item) for item in (stored_facts.get("quality_flags") or [])]
    changed = any(
        [
            artifact.result.decision != payload.decision,
            int(artifact.result.confidence_percent or 0) != int(payload.confidence_percent or 0),
            _normalize_text(artifact.result.summary_text) != _normalize_text("\n".join(payload.summary_points)),
            _normalize_text(artifact.result.facts_stack) != _normalize_text(facts_stack_after),
            _normalize_text(artifact.result.facts_requirements) != _normalize_text(facts_requirements_after),
            reason_codes_before != reason_codes_after,
            triage_before != list(facts.triage_signals or []),
            quality_before != list(facts.quality_flags or []),
        ]
    )
    return ArtifactRerenderDiff(
        title=artifact.result.title,
        url=artifact.result.url,
        artifact_path=str(artifact.path),
        schema_version=artifact.schema_version,
        stored_versions=dict(artifact.versions or {}),
        decision_before=artifact.result.decision,
        decision_after=payload.decision,
        confidence_before=int(artifact.result.confidence_percent or 0),
        confidence_after=int(payload.confidence_percent or 0),
        summary_changed=_normalize_text(artifact.result.summary_text) != _normalize_text("\n".join(payload.summary_points)),
        facts_stack_before=artifact.result.facts_stack,
        facts_stack_after=facts_stack_after,
        facts_requirements_before=artifact.result.facts_requirements,
        facts_requirements_after=facts_requirements_after,
        reason_codes_before=reason_codes_before,
        reason_codes_after=reason_codes_after,
        added_reason_codes=_diff_added(reason_codes_before, reason_codes_after),
        removed_reason_codes=_diff_added(reason_codes_after, reason_codes_before),
        triage_signals_before=triage_before,
        triage_signals_after=list(facts.triage_signals or []),
        added_signals=_diff_added(triage_before, list(facts.triage_signals or [])),
        removed_signals=_diff_added(list(facts.triage_signals or []), triage_before),
        quality_flags_before=quality_before,
        quality_flags_after=list(facts.quality_flags or []),
        changed=changed,
    )


def render_diff_report(rows: list[ArtifactRerenderDiff]) -> str:
    changed_rows = [row for row in rows if row.changed]
    lines = [
        "# artifact vs rerender diff",
        "",
        "## Summary",
        f"- compared: {len(rows)}",
        f"- changed: {len(changed_rows)}",
        "",
    ]
    if changed_rows:
        lines.extend(
            [
                "## Changed rows",
                "| Title | Decision | Confidence | Reasons + | Reasons - |",
                "|---|---|---:|---|---|",
            ]
        )
        for row in changed_rows:
            decision = f"{row.decision_before} -> {row.decision_after}"
            confidence = f"{row.confidence_before} -> {row.confidence_after}"
            lines.append(
                f"| {escape_md(row.title)} | {escape_md(decision)} | {escape_md(confidence)} | "
                f"{escape_md(', '.join(row.added_reason_codes) or '-')} | "
                f"{escape_md(', '.join(row.removed_reason_codes) or '-')} |"
            )
        lines.append("")
    for row in changed_rows[:20]:
        lines.extend(
            [
                f"## {row.title}",
                f"- artifact: `{row.artifact_path}`",
                f"- url: {row.url or '-'}",
                f"- schema_version: {row.schema_version}",
                f"- versions: `{json.dumps(row.stored_versions, ensure_ascii=False, sort_keys=True)}`",
                f"- decision: `{row.decision_before}` -> `{row.decision_after}`",
                f"- confidence: `{row.confidence_before}` -> `{row.confidence_after}`",
                f"- summary_changed: `{row.summary_changed}`",
                f"- added_reason_codes: {', '.join(row.added_reason_codes) or '-'}",
                f"- removed_reason_codes: {', '.join(row.removed_reason_codes) or '-'}",
                f"- added_signals: {', '.join(row.added_signals) or '-'}",
                f"- removed_signals: {', '.join(row.removed_signals) or '-'}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _build_fact_strings(facts: ExtractedFacts) -> tuple[str, str, str, str]:
    stack = ", ".join(facts.stack)
    req_parts = [*facts.turnover_requirements, *facts.project_requirements, *facts.team_requirements, *facts.licenses]
    reqs = "; ".join(req_parts)
    docs = "; ".join(facts.submission_requirements)
    payment = "; ".join(facts.payment)
    return stack, reqs, docs, payment


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def _diff_added(left: list[str], right: list[str]) -> list[str]:
    left_set = {str(item) for item in left}
    out: list[str] = []
    for item in right:
        normalized = str(item)
        if normalized not in left_set and normalized not in out:
            out.append(normalized)
    return out


def escape_md(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")
