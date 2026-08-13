from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tender_agent.artifact_diff import diff_artifact_vs_rerender, render_diff_report
from tender_agent.artifact_reader import iter_artifacts, unique_latest_artifacts
from tender_agent.batch_audit import audit_batch, write_batch_audit


@dataclass(frozen=True)
class QualityCheckResult:
    batch_name: str
    folders_dir: str
    xls_path: str
    artifacts_dir: str
    health: str
    xls_rows: int
    folders_found: int
    folders_with_files: int
    artifacts_written: int
    excel_rows: int
    issues_total: int
    rerender_compared: int
    rerender_changed: int
    rerender_changed_decisions: int
    rerender_changed_summaries: int
    artifacts_schema_v3: int
    artifacts_with_versions: int
    artifacts_with_reason_codes: int
    changed_titles: list[str]


def run_quality_check(
    *,
    batch_dir: Path,
    xls_path: Path,
    artifacts_root: Path,
    excel_path: Path | None = None,
) -> tuple[QualityCheckResult, str]:
    batch_dir = batch_dir.expanduser().resolve()
    xls_path = xls_path.expanduser().resolve()
    artifacts_root = artifacts_root.expanduser().resolve()
    excel_path = excel_path.expanduser().resolve() if excel_path else None

    audit = audit_batch(
        batch_dir=batch_dir,
        xls_path=xls_path,
        artifacts_root=artifacts_root,
        excel_path=excel_path,
    )

    artifacts = unique_latest_artifacts(iter_artifacts(artifacts_root, source="local_review", batch_name=batch_dir.name))
    diffs = []
    for artifact in artifacts:
        diff = diff_artifact_vs_rerender(artifact)
        if diff is not None:
            diffs.append(diff)

    changed = [row for row in diffs if row.changed]
    changed_decisions = [row for row in changed if row.decision_before != row.decision_after]
    changed_summaries = [row for row in changed if row.summary_changed]

    result = QualityCheckResult(
        batch_name=batch_dir.name,
        folders_dir=str(batch_dir),
        xls_path=str(xls_path),
        artifacts_dir=str(artifacts_root),
        health=audit.health,
        xls_rows=audit.xls_rows,
        folders_found=audit.folders_found,
        folders_with_files=audit.folders_with_files,
        artifacts_written=audit.artifacts_written,
        excel_rows=audit.excel_rows,
        issues_total=audit.issues_total,
        rerender_compared=len(diffs),
        rerender_changed=len(changed),
        rerender_changed_decisions=len(changed_decisions),
        rerender_changed_summaries=len(changed_summaries),
        artifacts_schema_v3=sum(1 for artifact in artifacts if int(artifact.schema_version or 0) >= 3),
        artifacts_with_versions=sum(1 for artifact in artifacts if artifact.versions),
        artifacts_with_reason_codes=sum(1 for artifact in artifacts if artifact.result.reason_codes),
        changed_titles=[row.title for row in changed[:50]],
    )

    report_lines = [
        "# quality check",
        "",
        "## Batch",
        f"- batch: `{batch_dir.name}`",
        f"- folders_dir: `{batch_dir}`",
        f"- xls_path: `{xls_path}`",
        f"- artifacts_dir: `{artifacts_root}`",
        "",
        "## Completeness",
        f"- health: `{audit.health}`",
        f"- xls_rows: `{audit.xls_rows}`",
        f"- folders_found: `{audit.folders_found}`",
        f"- folders_with_files: `{audit.folders_with_files}`",
        f"- artifacts_written: `{audit.artifacts_written}`",
        f"- excel_rows: `{audit.excel_rows}`",
        f"- issues_total: `{audit.issues_total}`",
        "",
        "## Artifact integrity",
        f"- schema_v3_or_newer: `{result.artifacts_schema_v3}` / `{len(artifacts)}`",
        f"- with_versions: `{result.artifacts_with_versions}` / `{len(artifacts)}`",
        f"- with_reason_codes: `{result.artifacts_with_reason_codes}` / `{len(artifacts)}`",
        "",
        "## Deterministic rerender diff",
        f"- compared: `{result.rerender_compared}`",
        f"- changed: `{result.rerender_changed}`",
        f"- changed_decisions: `{result.rerender_changed_decisions}`",
        f"- changed_summaries: `{result.rerender_changed_summaries}`",
        "",
    ]
    if result.changed_titles:
        report_lines.extend(
            [
                "## Changed titles",
                *[f"- {title}" for title in result.changed_titles],
                "",
            ]
        )
    report_lines.extend(
        [
            "## Diff details",
            render_diff_report(changed if changed else diffs).strip(),
            "",
        ]
    )
    return result, "\n".join(report_lines).strip() + "\n"


def write_quality_check(
    result: QualityCheckResult,
    report_markdown: str,
    *,
    batch_dir: Path,
) -> tuple[Path, Path]:
    batch_dir = batch_dir.expanduser().resolve()
    json_path = batch_dir / "_quality_check.json"
    md_path = batch_dir / "_quality_check.md"
    json_path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(report_markdown, encoding="utf-8")
    return json_path, md_path


def write_full_audit_bundle(
    result: QualityCheckResult,
    report_markdown: str,
    *,
    batch_dir: Path,
    audit_report: Any,
) -> tuple[Path, Path, Path, Path]:
    csv_path, audit_json_path = write_batch_audit(audit_report, batch_dir=batch_dir)
    quality_json_path, quality_md_path = write_quality_check(result, report_markdown, batch_dir=batch_dir)
    return csv_path, audit_json_path, quality_json_path, quality_md_path
