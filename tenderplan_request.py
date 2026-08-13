from __future__ import annotations

import argparse
import json
from pathlib import Path

from tender_agent.config import Settings
from tender_agent.tenderplan_api import TenderplanApiClient, TenderplanApiConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Tenderplan API request helper")
    parser.add_argument("path", help="API path, for example /api/v1/...")
    parser.add_argument("--method", default="GET", help="HTTP method")
    parser.add_argument(
        "--query",
        action="append",
        default=[],
        help="Query parameter in key=value form. Can be repeated.",
    )
    parser.add_argument(
        "--body-file",
        help="Path to JSON file with request body",
    )
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    settings = Settings.load(base_dir)
    client = TenderplanApiClient(
        TenderplanApiConfig(
            base_url=settings.tenderplan_base_url,
            pat=settings.tenderplan_pat,
            timeout_seconds=settings.tenderplan_timeout_seconds,
        )
    )

    query = _parse_pairs(args.query)
    json_body = None
    if args.body_file:
        json_body = json.loads(Path(args.body_file).read_text(encoding="utf-8"))

    response = client.request_json(
        args.path,
        method=args.method,
        query=query,
        json_body=json_body,
    )
    print(json.dumps(response, ensure_ascii=False, indent=2))


def _parse_pairs(items: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"Invalid --query value: {item}")
        key, value = item.split("=", 1)
        result[key] = value
    return result


if __name__ == "__main__":
    main()
