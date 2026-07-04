from __future__ import annotations

import json
import sys
from pathlib import Path

from tender_agent.artifact_reader import load_artifact
from tender_agent.rerender import rerender_artifact_result


def main() -> int:
    pack_path = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else Path("regression_pack.json").resolve()
    if not pack_path.exists():
        raise SystemExit(f"Regression pack not found: {pack_path}")

    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    failed = 0
    total = 0
    for case in pack:
        total += 1
        artifact_path = Path(case["artifact"]).expanduser().resolve()
        artifact = load_artifact(artifact_path)
        rerendered = rerender_artifact_result(artifact)
        if rerendered is None:
            print(f"[FAIL] {case['name']}: no facts in artifact")
            failed += 1
            continue
        payload, facts = rerendered
        errors: list[str] = []
        if payload.decision != case["expected_decision"]:
            errors.append(f"decision={payload.decision} expected={case['expected_decision']}")
        summary_text = "\n".join(payload.summary_points)
        stack_line = next((line for line in payload.summary_points if "Стек:" in line), "")
        requirements_line = next((line for line in payload.summary_points if "Требования к контрагенту:" in line), "")
        for token in case.get("expected_summary_contains", []):
            if token not in summary_text:
                errors.append(f"summary_missing={token}")
        for token in case.get("expected_stack_contains", []):
            if token not in stack_line:
                errors.append(f"stack_missing={token}")
        for token in case.get("expected_requirements_contains", []):
            if token not in requirements_line:
                errors.append(f"requirements_missing={token}")
        signals = facts.triage_signals or []
        for token in case.get("expected_signals_contains", []):
            if token not in signals:
                errors.append(f"signal_missing={token}")
        if errors:
            failed += 1
            print(f"[FAIL] {case['name']}")
            for error in errors:
                print(f"  - {error}")
        else:
            print(f"[OK] {case['name']}")
    print(f"total={total} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
