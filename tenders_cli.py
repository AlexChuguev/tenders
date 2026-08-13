from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from tender_agent.batch_audit import audit_batch, write_batch_audit
from tender_agent.config import Settings
from tender_agent.local_review import LocalTenderReviewer, _collect_files_from_prepared_folder
from tender_agent.quality_pipeline import run_quality_check, write_quality_check
from tender_agent.rerender import apply_rerender_to_result, rerender_artifact_result


def main() -> int:
    parser = argparse.ArgumentParser(description="Unified CLI for tender preparation, review and rebuild flows.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    batch_parser = subparsers.add_parser("review-batch", help="Run local batch review from prepared folders.")
    batch_parser.add_argument("folders_dir")
    batch_parser.add_argument("xls_path")
    batch_parser.add_argument("output_xlsx")
    batch_parser.add_argument("--deep", action="store_true")

    one_parser = subparsers.add_parser("review-one", help="Run local review for one tender/folder prefix.")
    one_parser.add_argument("folders_dir")
    one_parser.add_argument("xls_path")
    one_parser.add_argument("target")
    one_parser.add_argument("output_xlsx")
    one_parser.add_argument("--deep", action="store_true")

    audit_parser = subparsers.add_parser("audit-batch", help="Audit XLS -> folders -> artifacts -> Excel completeness.")
    audit_parser.add_argument("folders_dir")
    audit_parser.add_argument("xls_path")
    audit_parser.add_argument("--artifacts-dir")
    audit_parser.add_argument("--excel")

    quality_parser = subparsers.add_parser("quality-check", help="Run completeness + rerender quality checks for a batch.")
    quality_parser.add_argument("folders_dir")
    quality_parser.add_argument("xls_path")
    quality_parser.add_argument("--artifacts-dir")
    quality_parser.add_argument("--excel")
    quality_parser.add_argument("--write-report", action="store_true")

    rerender_parser = subparsers.add_parser(
        "rerender-batch",
        help="Deterministically rerender selected batch rows into an existing workbook or a new probe file.",
    )
    rerender_parser.add_argument("output_xlsx")
    rerender_parser.add_argument("--artifacts-dir")
    rerender_parser.add_argument("--batch", required=True)
    rerender_parser.add_argument(
        "--replace",
        action="store_true",
        help="Backward-compatible flag. Current behavior is safe in-place update; existing workbook is not deleted.",
    )

    args = parser.parse_args()
    base_dir = Path(__file__).resolve().parent
    settings = Settings.load(base_dir)

    if args.command == "review-batch":
        configured = _configure_settings(settings, args.folders_dir, args.xls_path, args.output_xlsx, args.deep)
        reviewer = LocalTenderReviewer(configured)
        reviewer.run()
        return 0

    if args.command == "review-one":
        configured = _configure_settings(settings, args.folders_dir, args.xls_path, args.output_xlsx, args.deep)
        return _review_one(configured, args.target)

    if args.command == "audit-batch":
        artifacts_dir = Path(args.artifacts_dir).expanduser().resolve() if args.artifacts_dir else settings.artifact_dir
        excel_path = Path(args.excel).expanduser().resolve() if args.excel else None
        report = audit_batch(
            batch_dir=Path(args.folders_dir).expanduser().resolve(),
            xls_path=Path(args.xls_path).expanduser().resolve(),
            artifacts_root=artifacts_dir,
            excel_path=excel_path,
        )
        csv_path, json_path = write_batch_audit(report, batch_dir=Path(args.folders_dir).expanduser().resolve())
        print(f"CSV: {csv_path}")
        print(f"JSON: {json_path}")
        print(f"Health: {report.health}")
        return 0

    if args.command == "quality-check":
        artifacts_dir = Path(args.artifacts_dir).expanduser().resolve() if args.artifacts_dir else settings.artifact_dir
        excel_path = Path(args.excel).expanduser().resolve() if args.excel else None
        result, markdown = run_quality_check(
            batch_dir=Path(args.folders_dir).expanduser().resolve(),
            xls_path=Path(args.xls_path).expanduser().resolve(),
            artifacts_root=artifacts_dir,
            excel_path=excel_path,
        )
        if args.write_report:
            json_path, md_path = write_quality_check(
                result,
                markdown,
                batch_dir=Path(args.folders_dir).expanduser().resolve(),
            )
            print(f"JSON: {json_path}")
            print(f"MD: {md_path}")
        else:
            print(markdown)
        return 0

    if args.command == "rerender-batch":
        return _rerender_batch(
            settings=settings,
            output_xlsx=Path(args.output_xlsx).expanduser().resolve(),
            artifacts_dir=Path(args.artifacts_dir).expanduser().resolve() if args.artifacts_dir else settings.artifact_dir,
            batch_name=args.batch,
            replace=args.replace,
        )

    return 2


def _configure_settings(settings: Settings, folders_dir: str, xls_path: str, output_xlsx: str, deep: bool) -> Settings:
    configured = replace(
        settings,
        local_files_dir=Path(folders_dir).expanduser().resolve(),
        input_xls=Path(xls_path).expanduser().resolve(),
        output_xlsx=Path(output_xlsx).expanduser().resolve(),
    )
    if deep:
        configured = replace(configured, review_mode="deep")
    return configured


def _review_one(settings: Settings, target: str) -> int:
    from review_one_tender import _find_folder, _find_tender
    from tender_agent.excel_loader import load_tenders

    reviewer = LocalTenderReviewer(settings)
    reviewer.sheet_writer.ensure_header()
    tenders = load_tenders(
        xls_path=settings.input_xls,
        url_column=settings.platform_tender_url_column,
        id_column=settings.platform_tender_id_column,
        title_column=settings.platform_tender_title_column,
    )
    tender = _find_tender(tenders, settings.local_files_dir, target)
    if tender is None:
        raise SystemExit(f"Tender not found for target: {target}")
    folder = _find_folder(settings.local_files_dir, tender.tender_id, target)
    files = _collect_files_from_prepared_folder(folder) if folder else []
    result = reviewer._process_one(tender, files, None, {})
    if result is None:
        return 1
    reviewer.sheet_writer.append_result(result)
    print(f"{result.tender_id}: {result.decision} {result.confidence_percent}%")
    return 0


def _rerender_batch(*, settings: Settings, output_xlsx: Path, artifacts_dir: Path, batch_name: str, replace: bool) -> int:
    from tender_agent.artifact_reader import iter_artifacts, unique_latest_artifacts
    from tender_agent.export.excel_writer import ExcelWriter

    # Safe behavior: rerender updates or inserts only rows from the selected batch
    # inside the target workbook. It must not rebuild the entire workbook from scratch.
    # The legacy `--replace` flag is intentionally ignored for existing files to prevent
    # accidental data loss in the user's working Excel.
    writer = ExcelWriter(output_path=output_xlsx)
    writer.ensure_header()
    artifacts = unique_latest_artifacts(iter_artifacts(artifacts_dir, source="local_review", batch_name=batch_name))
    written = 0
    for artifact in artifacts:
        rerendered = rerender_artifact_result(artifact)
        if rerendered is None:
            writer.append_result(artifact.result)
            written += 1
            continue
        payload, facts = rerendered
        apply_rerender_to_result(artifact, payload, facts)
        writer.append_result(artifact.result)
        written += 1
    print(f"Rerendered {written} rows into {output_xlsx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
