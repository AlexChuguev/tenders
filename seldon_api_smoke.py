from __future__ import annotations

import json
from pathlib import Path

from tender_agent.config import Settings
from tender_agent.seldon_api import SeldonApiClient, SeldonApiConfig


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    settings = Settings.load(base_dir)
    client = SeldonApiClient(
        SeldonApiConfig(
            base_url=settings.seldon_api_base_url,
            login=settings.seldon_api_login,
            password=settings.seldon_api_password,
            timeout_seconds=settings.seldon_api_timeout_seconds,
        )
    )

    token = client.login(force=True)
    balance = client.request_json("/User/Balance")
    filters = client.request_json("/User/Filters")

    summary = {
        "base_url": settings.seldon_api_base_url,
        "token_prefix": token[:8],
        "balance_status": (balance or {}).get("status"),
        "balance_result_keys": sorted(((balance or {}).get("result") or {}).keys()),
        "filters_status": (filters or {}).get("status"),
        "filters_result_keys": sorted(((filters or {}).get("result") or {}).keys()),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
