from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tender_agent.config import Settings
from tender_agent.seldon_api import SeldonApiClient, SeldonApiConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe Seldon Tenders API filters and purchase order flow")
    parser.add_argument("--filter-id", type=int, help="Filter ID to use for Purchases/New")
    parser.add_argument("--task-id", help="Existing taskId to poll without creating a new Purchases/New order")
    parser.add_argument("--report-id", type=int, help="Report ID for direct Purchases/Get or PurchasesDocuments/Get")
    parser.add_argument("--seldon-id", type=int, help="Seldon purchase ID for direct Purchases/Get or PurchasesDocuments/Get")
    parser.add_argument("--purchase-documents-get", action="store_true", help="Call /PurchasesDocuments/Get instead of /Purchases/Get")
    parser.add_argument("--result-only", action="store_true", help="Call Purchases/Result immediately for an existing taskId")
    parser.add_argument("--page-index", type=int, default=1, help="Result page index, 1-based")
    parser.add_argument("--days", type=int, default=1, help="How many days back to request, max 30")
    parser.add_argument("--subtype", type=int, choices=[1, 2], help="Subtype for reportId=2 filters")
    parser.add_argument("--poll-seconds", type=int, default=2, help="Polling interval for status")
    parser.add_argument("--poll-attempts", type=int, default=10, help="Max polling attempts")
    args = parser.parse_args()

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

    filters_payload = client.request_json("/User/Filters")
    filters = ((filters_payload or {}).get("result") or {}).get("filters") or []

    output: dict[str, object] = {
        "filters_count": len(filters),
        "filters_sample": filters[:20],
    }

    if args.report_id and args.seldon_id:
        payload = {
            "reportId": args.report_id,
            "seldonId": args.seldon_id,
        }
        if args.purchase_documents_get:
            response = client.request_json("/PurchasesDocuments/Get", json_body=payload)
            output["purchase_documents_get"] = response
        else:
            response = client.request_json("/Purchases/Get", json_body=payload)
            output["purchases_get"] = response
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    order_id = args.task_id
    if not order_id:
        filter_id = args.filter_id
        if filter_id is None and filters:
            for item in filters:
                if str(item.get("reportId")) in {"1", "2", "3", "5"}:
                    filter_id = int(item["filterId"])
                    break

        if filter_id is None:
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return

        chosen_filter = next((f for f in filters if int(f.get("filterId", 0)) == filter_id), None)
        output["chosen_filter"] = chosen_filter

        now = datetime.now(timezone.utc)
        date_to = now
        date_from = now - timedelta(days=max(0, min(args.days, 30)))
        payload = {
            "filterId": filter_id,
            "dateFrom": date_from.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "dateTo": date_to.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }
        if args.subtype is not None:
            payload["subtype"] = args.subtype

        new_payload = client.request_json("/Purchases/New", json_body=payload)
        output["new_response"] = new_payload

        order_info = ((new_payload or {}).get("result") or {})
        order_id = (
            order_info.get("taskId")
            or order_info.get("requestId")
            or order_info.get("orderId")
            or order_info.get("id")
            or order_info.get("guid")
        )
    if not order_id:
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    output["order_id"] = order_id

    status_payload = None
    result_payload = None
    if args.result_only:
        result_payload = client.request_json("/Purchases/Result", json_body=_result_payload(order_id, args.page_index))
        output["result_status"] = (result_payload or {}).get("status")
        result = ((result_payload or {}).get("result") or {})
        output["result_keys"] = sorted(result.keys()) if isinstance(result, dict) else []
        if isinstance(result, dict):
            for key in ("purchases", "items", "data"):
                value = result.get(key)
                if isinstance(value, list):
                    output["result_count"] = len(value)
                    output["result_sample"] = value[:3]
                    break
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    for attempt in range(args.poll_attempts):
        status_payload = client.request_json("/Purchases/Status", json_body=_status_payload(order_id))
        output["last_status_response"] = status_payload
        status_result = ((status_payload or {}).get("result") or {})
        if _is_ready(status_result):
            result_payload = client.request_json("/Purchases/Result", json_body=_result_payload(order_id, args.page_index))
            break
        time.sleep(args.poll_seconds)

    if result_payload is not None:
        output["result_status"] = (result_payload or {}).get("status")
        result = ((result_payload or {}).get("result") or {})
        output["result_keys"] = sorted(result.keys()) if isinstance(result, dict) else []
        if isinstance(result, dict):
            for key in ("purchases", "items", "data"):
                value = result.get(key)
                if isinstance(value, list):
                    output["result_count"] = len(value)
                    output["result_sample"] = value[:3]
                    break

    print(json.dumps(output, ensure_ascii=False, indent=2))


def _is_ready(status_result: dict[str, object]) -> bool:
    search_status = status_result.get("searchStatus")
    if isinstance(search_status, dict):
        code = search_status.get("code")
        if code == 3 or str(code) == "3":
            return True
    text = json.dumps(status_result, ensure_ascii=False).lower()
    if any(token in text for token in ["готов", "complete", "completed", "finished", "\"status\":200"]):
        return True
    for key in ("isCompleted", "completed", "isReady", "ready"):
        value = status_result.get(key)
        if value is True:
            return True
    return False


def _status_payload(order_id: str) -> dict[str, str]:
    return {
        "taskId": order_id,
    }


def _result_payload(order_id: str, page_index: int) -> dict[str, object]:
    return {
        "taskId": order_id,
        "pageIndex": max(1, page_index),
    }


if __name__ == "__main__":
    main()
