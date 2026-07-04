from __future__ import annotations

from pathlib import Path

from tender_agent.analysis_types import AnalysisPayload, ExtractedFacts, FactHit, LLMFinding, ParticipantRequirements
from tender_agent.artifact_reader import StoredArtifact
from tender_agent.document_facts import build_participant_requirements
from tender_agent.evidence import build_fact_columns, build_requirement_fact_columns, render_summary_points_with_evidence
from tender_agent.policy import TriagePolicy, load_triage_policy
from tender_agent.triage_rules import postprocess_payload


def extract_facts_from_artifact(artifact: StoredArtifact) -> ExtractedFacts | None:
    analysis = artifact.analysis or {}
    facts_payload = analysis.get("facts") or artifact.extra.get("facts") or {}
    if not isinstance(facts_payload, dict) or not facts_payload:
        return None
    participant_payload = facts_payload.get("participant_requirements") or {}
    participant_requirements = (
        ParticipantRequirements(**participant_payload)
        if participant_payload
        else build_participant_requirements(
            lower_text=" ".join(
                [
                    *[str(item) for item in (facts_payload.get("team_requirements") or [])],
                    *[str(item) for item in (facts_payload.get("project_requirements") or [])],
                    *[str(item) for item in (facts_payload.get("turnover_requirements") or [])],
                    *[str(item) for item in (facts_payload.get("licenses") or [])],
                    *[str(item) for item in (facts_payload.get("triage_signals") or [])],
                ]
            ).casefold().replace("ё", "е"),
            document_roles={
                str(key): [str(item) for item in (values or [])]
                for key, values in (facts_payload.get("document_roles") or {}).items()
            },
            turnover=[str(item) for item in (facts_payload.get("turnover_requirements") or [])],
            projects=[str(item) for item in (facts_payload.get("project_requirements") or [])],
            team=[str(item) for item in (facts_payload.get("team_requirements") or [])],
            licenses=[str(item) for item in (facts_payload.get("licenses") or [])],
        )
    )
    return ExtractedFacts(
        procurement_type=str(facts_payload.get("procurement_type") or "не удалось определить"),
        key_files=[str(item) for item in (facts_payload.get("key_files") or [])],
        stack=[str(item) for item in (facts_payload.get("stack") or [])],
        turnover_requirements=[str(item) for item in (facts_payload.get("turnover_requirements") or [])],
        project_requirements=[str(item) for item in (facts_payload.get("project_requirements") or [])],
        team_requirements=[str(item) for item in (facts_payload.get("team_requirements") or [])],
        licenses=[str(item) for item in (facts_payload.get("licenses") or [])],
        payment=[str(item) for item in (facts_payload.get("payment") or [])],
        document_roles={
            str(key): [str(item) for item in (values or [])]
            for key, values in (facts_payload.get("document_roles") or {}).items()
        },
        completeness_label=str(facts_payload.get("completeness_label") or artifact.result.document_completeness or "низкая"),
        completeness_notes=[str(item) for item in (facts_payload.get("completeness_notes") or [])],
        triage_signals=[str(item) for item in (facts_payload.get("triage_signals") or [])],
        participant_requirements=participant_requirements,
        submission_requirements=[str(item) for item in (facts_payload.get("submission_requirements") or [])],
        quality_flags=[str(item) for item in (facts_payload.get("quality_flags") or [])],
        reason_codes=[str(item) for item in (facts_payload.get("reason_codes") or artifact.result.reason_codes or [])],
        fact_hits=_load_fact_hits(facts_payload.get("fact_hits")),
    )


def _load_fact_hits(value) -> dict[str, list[FactHit]]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, list[FactHit]] = {}
    for field, rows in value.items():
        if not isinstance(rows, list):
            continue
        loaded: list[FactHit] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            loaded.append(
                FactHit(
                    field=str(row.get("field") or field),
                    term=str(row.get("term") or ""),
                    file_name=str(row.get("file_name") or ""),
                    fragment=str(row.get("fragment") or ""),
                    offset=int(row.get("offset") or 0),
                    page=int(row["page"]) if row.get("page") not in (None, "") else None,
                )
            )
        if loaded:
            out[str(field)] = loaded
    return out


def rerender_artifact_result(
    artifact: StoredArtifact,
    *,
    policy: TriagePolicy | None = None,
    evidence_files: list[Path] | None = None,
) -> tuple[AnalysisPayload, ExtractedFacts] | None:
    facts = extract_facts_from_artifact(artifact)
    if facts is None:
        return None
    analysis = artifact.analysis or {}
    payload = AnalysisPayload(
        decision=str(analysis.get("decision") or artifact.result.decision or "Уточнить"),
        confidence_percent=int(analysis.get("confidence_percent") or artifact.result.confidence_percent or 0),
        summary_points=list(analysis.get("summary_points") or []),
        analysis_markdown=str(analysis.get("analysis_markdown") or artifact.result.analysis_markdown or ""),
        completeness_label=str(
            analysis.get("completeness_label") or artifact.result.document_completeness or facts.completeness_label
        ),
        facts=facts,
        llm_raw_text=str(analysis.get("llm_raw_text") or ""),
        error_type=str(analysis.get("error_type") or artifact.result.error or ""),
        extraction_report=dict(analysis.get("extraction_report") or artifact.extra.get("extraction_report") or {}),
        llm_findings=_load_llm_findings(analysis.get("llm_findings")),
    )
    rerendered = postprocess_payload(payload, facts, policy=policy or load_triage_policy())
    files = evidence_files or [
        Path(item).expanduser().resolve()
        for item in artifact.result.downloaded_files
        if item and Path(item).expanduser().resolve().exists()
    ]
    rerendered.summary_points = render_summary_points_with_evidence(
        facts=facts,
        decision=rerendered.decision,
        files=files,
        deadline_at=artifact.result.deadline_at,
    )
    return rerendered, facts


def _load_llm_findings(value) -> list[LLMFinding]:
    if not isinstance(value, list):
        return []
    out: list[LLMFinding] = []
    for row in value:
        if not isinstance(row, dict):
            continue
        out.append(
            LLMFinding(
                category=str(row.get("category") or ""),
                text=str(row.get("text") or ""),
                quote=str(row.get("quote") or ""),
                source_file=str(row.get("source_file") or ""),
                verified=bool(row.get("verified")),
                reason=str(row.get("reason") or ""),
            )
        )
    return out


def apply_rerender_to_result(
    artifact: StoredArtifact,
    payload: AnalysisPayload,
    facts: ExtractedFacts,
) -> None:
    facts_stack, facts_requirements, facts_docs, facts_payment = build_fact_columns(facts)
    req_sro, req_turnover, req_analog, req_legacy, req_roles, req_licenses = build_requirement_fact_columns(facts)
    artifact.result.decision = payload.decision
    artifact.result.confidence_percent = payload.confidence_percent
    artifact.result.summary_text = "\n".join(payload.summary_points)
    artifact.result.document_completeness = payload.completeness_label
    artifact.result.analysis_markdown = payload.analysis_markdown
    artifact.result.facts_stack = facts_stack
    artifact.result.facts_requirements = facts_requirements
    artifact.result.facts_docs = facts_docs
    artifact.result.facts_payment = facts_payment
    artifact.result.facts_req_sro = req_sro
    artifact.result.facts_req_turnover = req_turnover
    artifact.result.facts_req_analog_projects = req_analog
    artifact.result.facts_req_legacy_experience = req_legacy
    artifact.result.facts_req_roles = req_roles
    artifact.result.facts_req_licenses = req_licenses
    artifact.result.reason_codes = list(facts.reason_codes)
