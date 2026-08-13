from __future__ import annotations

import argparse
from pathlib import Path

from tender_agent.analysis import TenderAnalyzer
from tender_agent.artifact_reader import (
    collect_policy_sha1s,
    iter_artifacts,
    unique_latest_artifacts,
)
from tender_agent.artifact_writer import ArtifactWriter
from tender_agent.config import Settings
from tender_agent.excel_writer import ExcelWriter
from tender_agent.models import TenderAnalysisResult


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reprocess stored artifacts with the current prompt/policy and write new artifacts."
    )
    parser.add_argument("--artifacts-dir", dest="artifacts_dir", help="Override artifacts root directory.")
    parser.add_argument("--source", dest="source", help="Filter artifacts by source.")
    parser.add_argument("--batch", dest="batch_name", help="Filter artifacts by batch name.")
    parser.add_argument("--policy-sha1", dest="policy_sha1", help="Filter artifacts by old policy sha1.")
    parser.add_argument("--output", dest="output_path", help="Output Excel file path.")
    parser.add_argument("--replace", action="store_true", help="Replace existing output file.")
    parser.add_argument(
        "--artifact-batch-name",
        dest="artifact_batch_name",
        help="Batch name for newly written reprocess artifacts.",
    )
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    settings = Settings.load(base_dir)
    artifacts_dir = Path(args.artifacts_dir).expanduser().resolve() if args.artifacts_dir else settings.artifact_dir
    output_path = Path(args.output_path).expanduser().resolve() if args.output_path else settings.output_xlsx

    selected = iter_artifacts(
        artifacts_dir,
        source=args.source,
        batch_name=args.batch_name,
        policy_sha1=args.policy_sha1,
    )
    if not selected:
        raise SystemExit("No artifacts found for reprocess scope.")

    latest = unique_latest_artifacts(selected)
    analyzer = TenderAnalyzer(
        provider_name=settings.llm_provider,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        prompt_template_path=settings.prompt_template_path,
        max_chars_per_file=settings.analysis_max_chars_per_file,
        triage_policy_path=settings.triage_policy_path,
    )
    artifact_batch_name = (
        args.artifact_batch_name
        or args.batch_name
        or "reprocess"
    )
    artifact_writer = ArtifactWriter(
        root=settings.artifact_dir,
        source="reprocess_artifacts",
        batch_name=artifact_batch_name,
        policy_metadata=analyzer.policy_metadata,
    )

    if output_path.exists():
        if not args.replace:
            raise SystemExit(f"Output file already exists: {output_path}. Pass --replace to overwrite.")
        output_path.unlink()
    writer = ExcelWriter(output_path=output_path)
    writer.ensure_header()

    processed = 0
    skipped_missing = 0
    previous_policy_sha1s = collect_policy_sha1s(selected)
    if len(previous_policy_sha1s) > 1 and not args.policy_sha1:
        print(
            "Warning: selected artifacts contain multiple old policy.sha1 values; "
            "reprocess will merge them into one new run."
        )

    for artifact in latest:
        files = [
            Path(item).expanduser().resolve()
            for item in artifact.result.downloaded_files
            if item
        ]
        files = [path for path in files if path.exists() and path.is_file()]
        if not files:
            skipped_missing += 1
            continue

        analysis = analyzer.analyze_with_context(
            tender_url=artifact.result.url,
            files=files,
            tender_title=artifact.result.title,
            tender_price_rub=artifact.result.nmck_rub,
        )
        result = TenderAnalysisResult(
            tender_id=artifact.result.tender_id,
            title=artifact.result.title,
            url=artifact.result.url,
            batch_name=artifact.result.batch_name or artifact.batch_name,
            batch_path=artifact.result.batch_path,
            deadline_at=artifact.result.deadline_at,
            customer=artifact.result.customer,
            customer_inn=artifact.result.customer_inn,
            decision=analysis.decision,
            confidence_percent=analysis.confidence_percent,
            summary_text="\n".join(analysis.summary_points),
            downloaded_files=[str(path) for path in files],
            analysis_markdown=analysis.analysis_markdown,
            nmck_rub=artifact.result.nmck_rub,
            document_completeness=analysis.completeness_label,
            error="",
            seldon_added_at=artifact.result.seldon_added_at,
            facts_stack="",
            facts_requirements="",
            facts_docs="",
            facts_payment="",
            reason_codes=list((analysis.facts.reason_codes if analysis.facts else [])),
        )
        artifact_writer.write(
            result=result,
            analysis=analysis,
            extra={
                "reprocessed_from": str(artifact.path),
                "previous_policy": artifact.policy,
                "previous_result": {
                    "decision": artifact.result.decision,
                    "confidence_percent": artifact.result.confidence_percent,
                    "summary_text": artifact.result.summary_text,
                },
            },
        )
        writer.append_result(result)
        processed += 1

    print(
        f"Reprocessed {processed} tenders into {output_path}; "
        f"skipped_missing_files={skipped_missing}; "
        f"input_artifacts={len(selected)}; latest_scope={len(latest)}."
    )


if __name__ == "__main__":
    main()
