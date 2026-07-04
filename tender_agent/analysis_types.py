from __future__ import annotations

from dataclasses import dataclass, field


DECISIONS = ["Брать", "Не брать", "Уточнить"]


@dataclass
class AnalysisPayload:
    decision: str
    confidence_percent: int
    summary_points: list[str]
    analysis_markdown: str
    completeness_label: str
    facts: "ExtractedFacts | None" = None
    llm_raw_text: str = ""
    error_type: str = ""
    extraction_report: dict[str, object] = field(default_factory=dict)
    llm_findings: list["LLMFinding"] = field(default_factory=list)


@dataclass
class LLMFinding:
    category: str
    text: str
    quote: str
    source_file: str = ""
    verified: bool = False
    reason: str = ""


@dataclass
class ParticipantRequirements:
    requires_sro: bool = False
    requires_analog_projects: bool = False
    requires_existing_system_modification_experience: bool = False
    requires_financial_documents: bool = False
    requires_team_documents: bool = False
    min_turnover_rub: int | None = None
    turnover_texts: list[str] = field(default_factory=list)
    experience_texts: list[str] = field(default_factory=list)
    required_roles: list[str] = field(default_factory=list)
    licenses_or_certs: list[str] = field(default_factory=list)
    other_requirements: list[str] = field(default_factory=list)
    submission_docs: list[str] = field(default_factory=list)


@dataclass
class FactHit:
    field: str
    term: str
    file_name: str
    fragment: str
    offset: int = 0
    page: int | None = None


@dataclass
class ExtractedFacts:
    procurement_type: str
    key_files: list[str]
    stack: list[str]
    turnover_requirements: list[str]
    project_requirements: list[str]
    team_requirements: list[str]
    licenses: list[str]
    payment: list[str]
    document_roles: dict[str, list[str]]
    completeness_label: str
    completeness_notes: list[str]
    triage_signals: list[str]
    participant_requirements: ParticipantRequirements = field(default_factory=ParticipantRequirements)
    submission_requirements: list[str] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    fact_hits: dict[str, list[FactHit]] = field(default_factory=dict)
