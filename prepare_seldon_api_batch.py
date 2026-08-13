from __future__ import annotations

import csv
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tender_agent.config import Settings
from tender_agent.search_profile import SearchProfile, SearchProfileMatch, evaluate_text
from tender_agent.seldon_api import SeldonApiClient, SeldonApiConfig, SeldonApiError


POLL_ATTEMPTS = 60
POLL_SECONDS = 5
RESULTS_PAGE_LIMIT = 120
LOOKBACK_DAYS = 30
TASK_RETRIES = 3
DEFAULT_FILTER_IDS = (4779897, 4779896, 4779898)
STATE_DIRNAME = "state"
TASK_STATE_FILENAME = "seldon_prepare_tasks.json"


@dataclass(frozen=True)
class SeldonApiCandidate:
    api_seldon_id: int
    report_id: int
    filter_id: int
    notification_number: str
    title: str
    url: str
    deadline_at: datetime | None
    customer: str
    customer_inn: str
    match: SearchProfileMatch
    raw: dict[str, Any]


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    settings = Settings.load(base_dir)
    output_root = (
        Path(sys.argv[1]).expanduser().resolve()
        if len(sys.argv) > 1
        else base_dir / "manual_downloads" / f"seldon_api_{datetime.now().strftime('%Y-%m-%d_%H.%M.%S')}"
    )
    output_root.mkdir(parents=True, exist_ok=True)

    client = SeldonApiClient(
        SeldonApiConfig(
            base_url=settings.seldon_api_base_url,
            login=settings.seldon_api_login,
            password=settings.seldon_api_password,
            timeout_seconds=settings.seldon_api_timeout_seconds,
        )
    )
    profile = SearchProfile.load(settings.search_profile_path)

    filters_payload = client.request_json("/User/Filters")
    filters = ((filters_payload or {}).get("result") or {}).get("filters") or []
    filter_map = _build_filter_map(filters)
    filter_ids = _resolve_filter_ids()
    task_state_path = _task_state_path(base_dir)
    task_state = _load_task_state(task_state_path)

    now = datetime.now(timezone.utc)
    lookback_days = _int_env("SELDON_PREPARE_LOOKBACK_DAYS", LOOKBACK_DAYS)
    date_from = (now - timedelta(days=lookback_days)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    date_to = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    all_candidates: dict[tuple[int, int], SeldonApiCandidate] = {}
    completed_filters: list[str] = []
    had_active_tasks = any(_has_active_task(task_state, int(key)) for key in task_state.keys() if key.isdigit())

    for filter_id_str, state in list(task_state.items()):
        try:
            filter_id = int(filter_id_str)
        except ValueError:
            continue
        filter_info = filter_map.get(filter_id)
        if filter_info is None:
            continue
        try:
            status_payload = client.request_json("/Purchases/Status", json_body={"taskId": state["task_id"]})
        except SeldonApiError as exc:
            print(f"[error] filterId={filter_id}: status check failed: {exc}", flush=True)
            continue
        result = ((status_payload or {}).get("result") or {})
        search_status = (result.get("searchStatus") or {}) if isinstance(result, dict) else {}
        code = search_status.get("code")
        descr = str(search_status.get("descr") or "").strip()
        state["last_status_code"] = code
        state["last_status_descr"] = descr
        state["last_checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not (code == 3 or str(code) == "3"):
            print(f"[wait] filterId={filter_id}: {descr or code or 'pending'}", flush=True)
            continue

        try:
            purchases = _fetch_ready_task_results(client, str(state["task_id"]))
        except SeldonApiError as exc:
            print(f"[error] filterId={filter_id}: result fetch failed: {exc}", flush=True)
            continue
        completed_filters.append(filter_id_str)
        print(f"[ready] filterId={filter_id}: {len(purchases)} purchases", flush=True)

        for purchase in purchases:
            candidate = _to_candidate(filter_id, purchase, profile)
            if candidate is None:
                continue
            key = (candidate.report_id, candidate.api_seldon_id)
            all_candidates[key] = candidate

    for filter_id_str in completed_filters:
        task_state.pop(filter_id_str, None)

    if not had_active_tasks and not task_state:
        for filter_id in filter_ids:
            if filter_id not in filter_map:
                print(f"[skip] filterId={filter_id}: not found in Seldon API filters", flush=True)
                continue
            try:
                task_id = _create_task(client, filter_id, date_from=date_from, date_to=date_to)
            except SeldonApiError as exc:
                print(f"[error] filterId={filter_id}: failed to enqueue task: {exc}", flush=True)
                continue
            task_state[str(filter_id)] = {
                "task_id": task_id,
                "filter_id": filter_id,
                "date_from": date_from,
                "date_to": date_to,
                "created_at": now.isoformat(timespec="seconds"),
                "last_status_code": 1,
                "last_status_descr": "wait",
            }
            print(f"[queue] filterId={filter_id}: created task {task_id}", flush=True)
    elif task_state:
        print(f"[queue] active tasks preserved: {', '.join(sorted(task_state.keys()))}", flush=True)

    _save_task_state(task_state_path, task_state)

    candidates = sorted(
        all_candidates.values(),
        key=lambda item: (item.deadline_at is None, item.deadline_at or datetime.max, item.title.lower()),
    )
    _write_manifest(output_root, candidates)
    print(f"Created {len(candidates)} Seldon API folders in {output_root}")
    print(f"Manifest: {output_root / '_manifest.csv'}")


def _resolve_filter_ids() -> list[int]:
    raw = (Path(__file__).resolve().parent / ".env").read_text(encoding="utf-8")
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("SELDON_FILTER_IDS="):
            continue
        value = line.split("=", 1)[1].strip()
        result: list[int] = []
        for chunk in value.split(","):
            chunk = chunk.strip()
            if chunk.isdigit():
                result.append(int(chunk))
        if result:
            return result
    return list(DEFAULT_FILTER_IDS)


def _build_filter_map(filters: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for item in filters:
        try:
            filter_id = int(item.get("filterId"))
        except Exception:
            continue
        result[filter_id] = item
    return result


def _fetch_ready_task_results(client: SeldonApiClient, task_id: str) -> list[dict[str, Any]]:
    page_limit = _int_env("SELDON_PREPARE_RESULTS_PAGE_LIMIT", RESULTS_PAGE_LIMIT)
    purchases: list[dict[str, Any]] = []
    for page_index in range(1, page_limit + 1):
        try:
            result_payload = client.request_json(
                "/Purchases/Result",
                json_body={"taskId": task_id, "pageIndex": page_index},
            )
        except SeldonApiError as exc:
            message = str(exc)
            if "404" in message and "Нет данных по странице" in message:
                break
            raise
        result = ((result_payload or {}).get("result") or {})
        page_purchases = result.get("purchases")
        if not isinstance(page_purchases, list) or not page_purchases:
            break
        purchases.extend(item for item in page_purchases if isinstance(item, dict))
    return purchases


def _create_task(client: SeldonApiClient, filter_id: int, *, date_from: str, date_to: str) -> str:
    response = client.request_json(
        "/Purchases/New",
        json_body={
            "filterId": filter_id,
            "dateFrom": date_from,
            "dateTo": date_to,
        },
    )
    task_id = str((((response or {}).get("result") or {}).get("taskId")) or "").strip()
    if not task_id:
        raise SeldonApiError(
            f"Purchases/New did not return taskId for filterId={filter_id}: "
            f"{json.dumps(response, ensure_ascii=False)}"
        )
    return task_id


def _to_candidate(filter_id: int, purchase: dict[str, Any], profile: SearchProfile) -> SeldonApiCandidate | None:
    if not _is_current_purchase(purchase):
        return None

    text = _compose_search_text(purchase)
    match = evaluate_text(profile, text)
    if not match.is_candidate:
        return None

    try:
        api_seldon_id = int(purchase.get("SeldonId"))
        report_id = int(purchase.get("reportId"))
    except Exception:
        return None

    notification_number = str(purchase.get("notificationNumber") or "").strip()
    title = str(purchase.get("subject") or "").strip()
    if not title:
        return None

    purchase_link = str(purchase.get("purchaseLink") or "").strip()
    deadline_at = _parse_api_datetime(purchase.get("endDate"))
    organizer = purchase.get("organizer") or {}
    customer = str(organizer.get("name") or "").strip()
    customer_inn = str(organizer.get("inn") or "").strip()
    url = _build_pro_url(purchase)

    return SeldonApiCandidate(
        api_seldon_id=api_seldon_id,
        report_id=report_id,
        filter_id=filter_id,
        notification_number=notification_number,
        title=title,
        url=url or purchase_link,
        deadline_at=deadline_at,
        customer=customer,
        customer_inn=customer_inn,
        match=match,
        raw=purchase,
    )


def _is_current_purchase(purchase: dict[str, Any]) -> bool:
    status = purchase.get("status") or {}
    if isinstance(status, dict):
        code = status.get("codeStatusSeldon")
        if code == 1 or str(code) == "1":
            return True
        name = str(status.get("statusSeldon") or "").strip().lower()
        if "текущ" in name:
            return True
    return False


def _compose_search_text(purchase: dict[str, Any]) -> str:
    parts = [
        str(purchase.get("subject") or ""),
        str(purchase.get("notificationNumber") or ""),
    ]
    for lot in purchase.get("lotsList") or []:
        if not isinstance(lot, dict):
            continue
        parts.append(str(lot.get("subject") or ""))
    return " ".join(part for part in parts if part)


def _build_pro_url(purchase: dict[str, Any]) -> str:
    report_id = purchase.get("reportId")
    for lot in purchase.get("lotsList") or []:
        if not isinstance(lot, dict):
            continue
        lot_id = lot.get("id")
        if lot_id:
            return f"https://pro.myseldon.com/ru/tender/{lot_id}-{report_id}"
    return ""


def _write_manifest(output_root: Path, candidates: list[SeldonApiCandidate]) -> None:
    used_names: set[str] = set()
    manifest_rows: list[dict[str, str]] = []
    for index, candidate in enumerate(candidates, start=1):
        base_name = f"{index:03d}. {_sanitize_folder_name(candidate.title)}"
        folder_name = _make_unique_folder_name(base_name, used_names)
        (output_root / folder_name).mkdir(exist_ok=True)
        manifest_rows.append(
            {
                "order": str(index),
                "tender_id": str(candidate.api_seldon_id),
                "title": candidate.title,
                "folder_name": folder_name,
                "deadline_at": candidate.deadline_at.strftime("%Y-%m-%d %H:%M") if candidate.deadline_at else "",
                "url": candidate.url,
                "report_id": str(candidate.report_id),
                "api_seldon_id": str(candidate.api_seldon_id),
                "notification_number": candidate.notification_number,
                "filter_id": str(candidate.filter_id),
                "customer": candidate.customer,
                "customer_inn": candidate.customer_inn,
                "purchase_link": str(candidate.raw.get("purchaseLink") or ""),
            }
        )

    manifest_path = output_root / "_manifest.csv"
    with manifest_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "order",
                "tender_id",
                "title",
                "folder_name",
                "deadline_at",
                "url",
                "report_id",
                "api_seldon_id",
                "notification_number",
                "filter_id",
                "customer",
                "customer_inn",
                "purchase_link",
            ],
        )
        writer.writeheader()
        writer.writerows(manifest_rows)


def _parse_api_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _sanitize_folder_name(value: str) -> str:
    sanitized = value.replace("/", "／").replace("\0", "").strip().rstrip(". ")
    return _truncate_utf8(sanitized or "Без названия", max_bytes=180) or "Без названия"


def _make_unique_folder_name(name: str, used_names: set[str]) -> str:
    candidate = name
    suffix = 2
    while candidate in used_names:
        candidate = f"{name} ({suffix})"
        suffix += 1
    used_names.add(candidate)
    return candidate


def _truncate_utf8(value: str, max_bytes: int) -> str:
    raw = value.encode("utf-8")
    if len(raw) <= max_bytes:
        return value
    ellipsis = "..."
    trimmed = raw[: max_bytes - len(ellipsis.encode("utf-8"))]
    while trimmed:
        try:
            return trimmed.decode("utf-8").rstrip() + ellipsis
        except UnicodeDecodeError:
            trimmed = trimmed[:-1]
    return ellipsis


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _task_state_path(base_dir: Path) -> Path:
    state_dir = base_dir / STATE_DIRNAME
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / TASK_STATE_FILENAME


def _load_task_state(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, value in payload.items():
        if isinstance(key, str) and isinstance(value, dict):
            result[key] = value
    return result


def _save_task_state(path: Path, state: dict[str, dict[str, Any]]) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _has_active_task(state: dict[str, dict[str, Any]], filter_id: int) -> bool:
    entry = state.get(str(filter_id))
    if not isinstance(entry, dict):
        return False
    task_id = str(entry.get("task_id") or "").strip()
    return bool(task_id)


if __name__ == "__main__":
    main()
