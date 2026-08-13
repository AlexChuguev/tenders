from __future__ import annotations

import argparse
from pathlib import Path

from tender_agent.artifact_diff import diff_artifact_vs_rerender, render_diff_report
from tender_agent.artifact_reader import iter_artifacts, unique_latest_artifacts
from tender_agent.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare stored artifacts against deterministic rerender from current rules.")
    parser.add_argument("--artifacts-dir", dest="artifacts_dir", help="Override artifacts root directory.")
    parser.add_argument("--source", dest="source", help="Filter artifacts by source.")
    parser.add_argument("--batch", dest="batch_name", help="Filter artifacts by batch name.")
    parser.add_argument("--only-changed", action="store_true", help="Render only changed rows.")
    parser.add_argument("--output", dest="output_path", help="Write markdown report to file.")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    settings = Settings.load(base_dir)
    artifacts_dir = Path(args.artifacts_dir).expanduser().resolve() if args.artifacts_dir else settings.artifact_dir
    artifacts = unique_latest_artifacts(iter_artifacts(artifacts_dir, source=args.source, batch_name=args.batch_name))
    if not artifacts:
        raise SystemExit("No artifacts found for selected scope.")

    rows = []
    for artifact in artifacts:
        diff = diff_artifact_vs_rerender(artifact)
        if diff is None:
            continue
        if args.only_changed and not diff.changed:
            continue
        rows.append(diff)
    report = render_diff_report(rows)
    if args.output_path:
        output_path = Path(args.output_path).expanduser().resolve()
        output_path.write_text(report, encoding="utf-8")
        print(f"Wrote diff report to {output_path}")
        return
    print(report)


if __name__ == "__main__":
    main()
