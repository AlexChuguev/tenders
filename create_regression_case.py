from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from tender_agent.artifact_reader import load_artifact
from tender_agent.rerender import rerender_artifact_result


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a regression case skeleton from an artifact.")
    parser.add_argument("artifact", help="Path to artifact json")
    parser.add_argument("--name", help="Stable regression case name")
    parser.add_argument("--decision", help="Override expected decision")
    parser.add_argument("--summary", action="append", dest="summary_tokens", default=[], help="Expected token in full summary")
    parser.add_argument("--stack", action="append", dest="stack_tokens", default=[], help="Expected token in stack line")
    parser.add_argument(
        "--requirements",
        action="append",
        dest="requirements_tokens",
        default=[],
        help="Expected token in requirements line",
    )
    parser.add_argument("--signal", action="append", dest="signals", default=[], help="Expected triage signal")
    args = parser.parse_args()

    artifact_path = Path(args.artifact).expanduser().resolve()
    if not artifact_path.exists():
        raise SystemExit(f"Artifact not found: {artifact_path}")

    artifact = load_artifact(artifact_path)
    rerendered = rerender_artifact_result(artifact)
    if rerendered is None:
        raise SystemExit("Artifact does not contain rerenderable facts.")
    payload, facts = rerendered

    case_name = args.name or _slugify(artifact.result.title)
    case = {
        "name": case_name,
        "artifact": str(artifact_path.relative_to(Path.cwd())),
        "expected_decision": args.decision or payload.decision,
        "expected_summary_contains": args.summary_tokens,
        "expected_stack_contains": args.stack_tokens,
        "expected_requirements_contains": args.requirements_tokens,
        "expected_signals_contains": args.signals,
    }

    if not case["expected_summary_contains"]:
        case["expected_summary_contains"] = _default_summary_tokens(payload.summary_points)
    if not case["expected_stack_contains"]:
        case["expected_stack_contains"] = _default_line_tokens(_find_line(payload.summary_points, "Стек:"))
    if not case["expected_requirements_contains"]:
        case["expected_requirements_contains"] = _default_line_tokens(
            _find_line(payload.summary_points, "Требования к контрагенту:")
        )
    if not case["expected_signals_contains"]:
        case["expected_signals_contains"] = list(facts.triage_signals[:3])

    print(json.dumps(case, ensure_ascii=False, indent=2))
    return 0


def _find_line(points: list[str], marker: str) -> str:
    return next((line for line in points if marker in line), "")


def _default_summary_tokens(points: list[str]) -> list[str]:
    tokens: list[str] = []
    for marker in ("Стек:", "Требования к контрагенту:"):
        line = _find_line(points, marker)
        tokens.extend(_default_line_tokens(line))
    return tokens[:4]


def _default_line_tokens(line: str) -> list[str]:
    if not line:
        return []
    text = re.sub(r"^\d+\.\s*", "", line)
    text = text.split("(", 1)[0]
    text = text.split(":", 1)[-1]
    parts = [part.strip() for part in re.split(r"[;,]", text) if part.strip()]
    return parts[:3]


def _slugify(value: str) -> str:
    lowered = value.casefold().replace("ё", "е")
    lowered = re.sub(r"[^a-zа-я0-9]+", "_", lowered)
    lowered = lowered.strip("_")
    return lowered[:80] or "regression_case"


if __name__ == "__main__":
    raise SystemExit(main())
