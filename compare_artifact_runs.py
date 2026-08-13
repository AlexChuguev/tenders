from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from tender_agent.artifact_reader import (
    StoredArtifact,
    iter_artifacts,
    latest_artifact_map,
)
from tender_agent.config import Settings
from tender_agent.models import TenderAnalysisResult


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare artifact results across runs or against embedded previous_result."
    )
    parser.add_argument("--artifacts-dir", dest="artifacts_dir", help="Override artifacts root directory.")
    parser.add_argument("--embedded-previous", action="store_true", help="Compare artifacts against embedded previous_result.")
    parser.add_argument("--left-source", dest="left_source", help="Left scope source.")
    parser.add_argument("--left-batch", dest="left_batch", help="Left scope batch.")
    parser.add_argument("--left-policy-sha1", dest="left_policy_sha1", help="Left scope policy sha1.")
    parser.add_argument("--right-source", dest="right_source", help="Right scope source.")
    parser.add_argument("--right-batch", dest="right_batch", help="Right scope batch.")
    parser.add_argument("--right-policy-sha1", dest="right_policy_sha1", help="Right scope policy sha1.")
    parser.add_argument("--output", dest="output_path", help="Write markdown report to file.")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    settings = Settings.load(base_dir)
    artifacts_dir = Path(args.artifacts_dir).expanduser().resolve() if args.artifacts_dir else settings.artifact_dir

    if args.embedded_previous:
        report = build_embedded_previous_report(
            root=artifacts_dir,
            source=args.right_source,
            batch_name=args.right_batch,
            policy_sha1=args.right_policy_sha1,
        )
    else:
        report = build_scope_diff_report(
            root=artifacts_dir,
            left_source=args.left_source,
            left_batch=args.left_batch,
            left_policy_sha1=args.left_policy_sha1,
            right_source=args.right_source,
            right_batch=args.right_batch,
            right_policy_sha1=args.right_policy_sha1,
        )

    if args.output_path:
        output_path = Path(args.output_path).expanduser().resolve()
        output_path.write_text(report, encoding="utf-8")
        print(f"Wrote diff report to {output_path}")
        return
    print(report)


def build_scope_diff_report(
    root: Path,
    *,
    left_source: str | None,
    left_batch: str | None,
    left_policy_sha1: str | None,
    right_source: str | None,
    right_batch: str | None,
    right_policy_sha1: str | None,
) -> str:
    left_artifacts = iter_artifacts(
        root,
        source=left_source,
        batch_name=left_batch,
        policy_sha1=left_policy_sha1,
    )
    right_artifacts = iter_artifacts(
        root,
        source=right_source,
        batch_name=right_batch,
        policy_sha1=right_policy_sha1,
    )
    if not left_artifacts:
        raise SystemExit("Left scope is empty.")
    if not right_artifacts:
        raise SystemExit("Right scope is empty.")

    left_map = latest_artifact_map(left_artifacts)
    right_map = latest_artifact_map(right_artifacts)
    rows = []
    all_keys = sorted(set(left_map) | set(right_map))
    for key in all_keys:
        left = left_map.get(key)
        right = right_map.get(key)
        rows.append(compare_pair(left, right))

    scope = {
        "left": {
            "source": left_source or "*",
            "batch": left_batch or "*",
            "policy_sha1": left_policy_sha1 or "*",
            "artifacts": len(left_artifacts),
            "latest": len(left_map),
        },
        "right": {
            "source": right_source or "*",
            "batch": right_batch or "*",
            "policy_sha1": right_policy_sha1 or "*",
            "artifacts": len(right_artifacts),
            "latest": len(right_map),
        },
    }
    return render_report("artifact scope diff", scope, rows)


def build_embedded_previous_report(
    root: Path,
    *,
    source: str | None,
    batch_name: str | None,
    policy_sha1: str | None,
) -> str:
    artifacts = iter_artifacts(
        root,
        source=source,
        batch_name=batch_name,
        policy_sha1=policy_sha1,
    )
    if not artifacts:
        raise SystemExit("Selected scope is empty.")

    rows = []
    latest = latest_artifact_map(artifacts)
    for artifact in latest.values():
        previous = _embedded_previous_result(artifact)
        if previous is None:
            continue
        rows.append(compare_pair_from_result(previous, artifact.result, artifact))

    scope = {
        "mode": "embedded_previous",
        "source": source or "*",
        "batch": batch_name or "*",
        "policy_sha1": policy_sha1 or "*",
        "artifacts": len(artifacts),
        "latest": len(latest),
        "comparable": len(rows),
    }
    return render_report("embedded previous diff", scope, rows)


def compare_pair(left: StoredArtifact | None, right: StoredArtifact | None) -> dict:
    if left is None and right is None:
        raise RuntimeError("compare_pair received two empty sides")
    left_result = left.result if left is not None else None
    right_result = right.result if right is not None else None
    return _compare_results(
        left_result=left_result,
        right_result=right_result,
        left_policy=(left.policy if left is not None else {}),
        right_policy=(right.policy if right is not None else {}),
    )


def compare_pair_from_result(
    left_result: TenderAnalysisResult,
    right_result: TenderAnalysisResult,
    right_artifact: StoredArtifact,
) -> dict:
    return _compare_results(
        left_result=left_result,
        right_result=right_result,
        left_policy=_coerce_mapping(right_artifact.extra.get("previous_policy")),
        right_policy=right_artifact.policy,
    )


def _compare_results(
    *,
    left_result: TenderAnalysisResult | None,
    right_result: TenderAnalysisResult | None,
    left_policy: dict,
    right_policy: dict,
) -> dict:
    base = right_result or left_result
    assert base is not None
    left_decision = left_result.decision if left_result else ""
    right_decision = right_result.decision if right_result else ""
    left_conf = left_result.confidence_percent if left_result else None
    right_conf = right_result.confidence_percent if right_result else None
    return {
        "title": base.title,
        "url": base.url,
        "deadline_at": _fmt_dt(base.deadline_at),
        "left_decision": left_decision,
        "right_decision": right_decision,
        "left_confidence": left_conf,
        "right_confidence": right_conf,
        "confidence_delta": _delta(left_conf, right_conf),
        "decision_changed": bool(left_result and right_result and left_decision != right_decision),
        "summary_changed": bool(
            left_result and right_result and normalize_text(left_result.summary_text) != normalize_text(right_result.summary_text)
        ),
        "missing_left": left_result is None,
        "missing_right": right_result is None,
        "left_policy_sha1": str(left_policy.get("sha1") or ""),
        "right_policy_sha1": str(right_policy.get("sha1") or ""),
    }


def render_report(title: str, scope: dict, rows: list[dict]) -> str:
    changed_decisions = [row for row in rows if row["decision_changed"]]
    changed_summary = [row for row in rows if row["summary_changed"]]
    changed_conf = [row for row in rows if row["confidence_delta"] not in (None, 0)]
    missing_left = [row for row in rows if row["missing_left"]]
    missing_right = [row for row in rows if row["missing_right"]]

    lines = [
        f"# {title}",
        "",
        "## Scope",
        "```json",
        _pretty_json(scope),
        "```",
        "",
        "## Summary",
        f"- compared: {len(rows)}",
        f"- decision_changed: {len(changed_decisions)}",
        f"- summary_changed: {len(changed_summary)}",
        f"- confidence_changed: {len(changed_conf)}",
        f"- missing_left: {len(missing_left)}",
        f"- missing_right: {len(missing_right)}",
        "",
    ]

    if changed_decisions:
        lines.extend([
            "## Decision changes",
            "| Title | Left | Right | Δ conf | Deadline |",
            "|---|---:|---:|---:|---|",
        ])
        for row in changed_decisions:
            lines.append(
                f"| {escape_md(row['title'])} | {row['left_decision']} | {row['right_decision']} | "
                f"{format_delta(row['confidence_delta'])} | {row['deadline_at']} |"
            )
        lines.append("")

    if changed_conf:
        top_conf = sorted(changed_conf, key=lambda row: abs(row["confidence_delta"] or 0), reverse=True)[:20]
        lines.extend([
            "## Confidence shifts",
            "| Title | Left | Right | Δ conf |",
            "|---|---:|---:|---:|",
        ])
        for row in top_conf:
            lines.append(
                f"| {escape_md(row['title'])} | {fmt_opt(row['left_confidence'])} | "
                f"{fmt_opt(row['right_confidence'])} | {format_delta(row['confidence_delta'])} |"
            )
        lines.append("")

    if missing_left or missing_right:
        lines.extend([
            "## Presence mismatches",
            "| Title | Missing left | Missing right | Deadline |",
            "|---|---:|---:|---|",
        ])
        for row in missing_left + missing_right:
            lines.append(
                f"| {escape_md(row['title'])} | {'yes' if row['missing_left'] else ''} | "
                f"{'yes' if row['missing_right'] else ''} | {row['deadline_at']} |"
            )
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _embedded_previous_result(artifact: StoredArtifact) -> TenderAnalysisResult | None:
    payload = _coerce_mapping(artifact.extra.get("previous_result"))
    if not payload:
        return None
    base = asdict(artifact.result)
    base.update(payload)
    return TenderAnalysisResult(**base)


def _coerce_mapping(value) -> dict:
    if isinstance(value, dict):
        return value
    return {}


def _delta(left: int | None, right: int | None) -> int | None:
    if left is None or right is None:
        return None
    return right - left


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%d.%m.%Y %H:%M")


def fmt_opt(value: int | None) -> str:
    if value is None:
        return ""
    return str(value)


def format_delta(value: int | None) -> str:
    if value is None:
        return ""
    if value > 0:
        return f"+{value}"
    return str(value)


def normalize_text(value: str) -> str:
    return " ".join(str(value).split())


def escape_md(value: str) -> str:
    return str(value).replace("|", "\\|")


def _pretty_json(value: dict) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
