from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tender_agent.artifact_reader import collect_policy_sha1s, iter_artifacts, unique_latest_results
from tender_agent.config import Settings
from tender_agent.excel_writer import ExcelWriter
from tender_agent.rerender import apply_rerender_to_result, rerender_artifact_result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild tender_analysis.xlsx from JSON artifacts."
    )
    parser.add_argument(
        "--artifacts-dir",
        dest="artifacts_dir",
        help="Override artifacts root directory.",
    )
    parser.add_argument(
        "--source",
        dest="source",
        help="Filter artifacts by source.",
    )
    parser.add_argument(
        "--batch",
        dest="batch_name",
        help="Filter artifacts by batch name.",
    )
    parser.add_argument(
        "--policy-sha1",
        dest="policy_sha1",
        help="Filter artifacts by triage policy sha1.",
    )
    parser.add_argument(
        "--output",
        dest="output_path",
        help="Output Excel file path.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace existing output file.",
    )
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    settings = Settings.load(base_dir)
    artifacts_dir = Path(args.artifacts_dir).expanduser().resolve() if args.artifacts_dir else settings.artifact_dir
    output_path = Path(args.output_path).expanduser().resolve() if args.output_path else settings.output_xlsx

    if output_path.exists():
        if not args.replace:
            raise SystemExit(f"Output file already exists: {output_path}. Pass --replace to overwrite.")
        output_path.unlink()

    artifacts = iter_artifacts(
        artifacts_dir,
        source=args.source,
        batch_name=args.batch_name,
        policy_sha1=args.policy_sha1,
    )
    if not artifacts:
        raise SystemExit("No artifacts found for the selected scope.")
    if not args.policy_sha1:
        policy_sha1s = collect_policy_sha1s(artifacts)
        if len(policy_sha1s) > 1:
            print(
                "Warning: selected artifacts contain multiple policy.sha1 values; "
                "rebuild may mix results from different triage rules. "
                "Use --policy-sha1 to pin one version.",
                file=sys.stderr,
            )

    results = unique_latest_results(artifacts)
    writer = ExcelWriter(output_path=output_path)
    writer.ensure_header()
    latest_artifacts = {artifact.result.url.strip() or artifact.result.tender_id.strip(): artifact for artifact in artifacts}
    for result in results:
        identity = result.url.strip() or result.tender_id.strip()
        artifact = latest_artifacts.get(identity)
        if artifact is not None:
            rerendered = rerender_artifact_result(artifact)
            if rerendered is not None:
                payload, facts = rerendered
                apply_rerender_to_result(artifact, payload, facts)
                result = artifact.result
        writer.append_result(result)

    print(
        f"Rebuilt {output_path} from {len(artifacts)} artifacts "
        f"({len(results)} unique results)."
    )


if __name__ == "__main__":
    main()
