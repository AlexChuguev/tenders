from __future__ import annotations

import csv
import json
import os
import re
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from playwright.sync_api import BrowserContext, Download, Error, Page, sync_playwright

from tender_agent.config import Settings
from tender_agent.seldon_api import SeldonApiClient, SeldonApiConfig, SeldonApiError


BROWSER_FALLBACK_HOSTS = (
    "rts-tender.ru",
    "gazprom-neft.ru",
)
DIRECT_DOWNLOAD_TIMEOUT_SECONDS = 45
POLL_ATTEMPTS = 60
POLL_SECONDS = 5
RESULTS_PAGE_LIMIT = 120
PRO_REPORT_TO_API_REPORT = {
    1: 1,
    2: 2,
    3: 3,
    4: 2,
}


@dataclass(frozen=True)
class ManifestRow:
    order: str
    tender_id: str
    title: str
    folder_name: str
    url: str
    report_id: str = ""
    api_seldon_id: str = ""


@dataclass(frozen=True)
class MappingTarget:
    manifest: ManifestRow
    report_id: int
    source_report_id: int
    pro_tender_id: str
    external_tender_id: str


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage: .venv/bin/python download_seldon_api_documents.py /path/to/prepared_folders [export.xls]"
        )

    base_dir = Path(__file__).resolve().parent
    settings = Settings.load(base_dir)
    prepared_root = Path(sys.argv[1]).expanduser().resolve()
    xls_path = Path(sys.argv[2]).expanduser().resolve() if len(sys.argv) > 2 else None

    if not prepared_root.exists():
        raise SystemExit(f"Prepared folders directory not found: {prepared_root}")
    if xls_path is not None and not xls_path.exists():
        raise SystemExit(f"XLS file not found: {xls_path}")

    client = SeldonApiClient(
        SeldonApiConfig(
            base_url=settings.seldon_api_base_url,
            login=settings.seldon_api_login,
            password=settings.seldon_api_password,
            timeout_seconds=settings.seldon_api_timeout_seconds,
        )
    )

    manifest_rows = _load_manifest(prepared_root / "_manifest.csv")
    targets = _build_targets(manifest_rows)
    if not targets:
        print("No Seldon.Pro targets were found in the prepared manifest.", flush=True)
        return

    mappings: dict[str, tuple[int, int]] = {}
    status_rows: list[dict[str, str]] = []
    needs_mapping = [target for target in targets if _resolve_from_manifest(target.manifest) is None]
    if needs_mapping:
        filters_payload = client.request_json("/User/Filters")
        filters = ((filters_payload or {}).get("result") or {}).get("filters") or []
        filter_by_report_id = _select_filters_by_report(filters)

        needed_by_report: dict[int, dict[str, MappingTarget]] = {}
        for target in needs_mapping:
            needed_by_report.setdefault(target.report_id, {})[target.pro_tender_id] = target

        for report_id, needed in needed_by_report.items():
            api_report_id = PRO_REPORT_TO_API_REPORT.get(report_id, report_id)
            filter_id = filter_by_report_id.get(api_report_id)
            if filter_id is None:
                print(f"[error] reportId={report_id}: no matching API filter", flush=True)
                continue
            try:
                found = _collect_mappings_for_report(client, filter_id, api_report_id, needed)
            except SeldonApiError as exc:
                print(f"[error] reportId={report_id}: mapping task failed: {exc}", flush=True)
                continue
            mappings.update(found)

    browser_bundle: tuple[Any, BrowserContext] | None = None
    try:
        for target in targets:
            folder = prepared_root / target.manifest.folder_name
            if _has_downloaded_files(folder):
                print(f"[skip] {target.manifest.order}: {target.manifest.folder_name} already has files", flush=True)
                status_rows.append(
                    _status_row(target, "existing_files", "", _count_valid_downloaded_files(folder))
                )
                continue

            mapped = mappings.get(target.pro_tender_id)
            if mapped is None:
                mapped = _resolve_from_manifest(target.manifest)
            if mapped is None:
                mapped = _resolve_by_external_tender_id(client, target)
            if mapped is None:
                print(
                    f"[error] {target.manifest.order}: API mapping not found for pro-id={target.pro_tender_id}",
                    flush=True,
                )
                status_rows.append(_status_row(target, "mapping_not_found", "", 0))
                continue

            report_id, seldon_id = mapped
            try:
                docs_payload = client.request_json(
                    "/PurchasesDocuments/Get",
                    json_body={"reportId": report_id, "seldonId": seldon_id},
                )
            except SeldonApiError as exc:
                print(f"[error] {target.manifest.order}: documents metadata request failed: {exc}", flush=True)
                status_rows.append(_status_row(target, "documents_metadata_failed", str(exc), 0))
                continue

            documents = _extract_documents(docs_payload)
            if not documents:
                print(f"[skip] {target.manifest.order}: API returned no documents", flush=True)
                status_rows.append(_status_row(target, "no_documents", "", 0))
                continue

            folder.mkdir(parents=True, exist_ok=True)
            saved = 0
            errors: list[str] = []
            for document in documents:
                source_url = str(document.get("urlSource") or "").strip()
                if not source_url:
                    continue
                doc_name = str(document.get("name") or document.get("id") or "document").strip()
                output_path = folder / _sanitize_filename(doc_name)
                if output_path.exists() and output_path.stat().st_size > 0:
                    if _is_valid_downloaded_file(output_path):
                        saved += 1
                        continue
                    output_path.unlink()
                try:
                    _download_document(source_url, output_path)
                    saved += 1
                    continue
                except Exception as exc:
                    host = urlparse(source_url).netloc.lower()
                    if any(token in host for token in BROWSER_FALLBACK_HOSTS):
                        if browser_bundle is None:
                            browser_bundle = _start_browser(base_dir, settings)
                        try:
                            _download_with_browser(browser_bundle[1], source_url, output_path)
                            saved += 1
                            continue
                        except Exception as browser_exc:
                            errors.append(str(browser_exc))
                            print(
                                f"[error] {target.manifest.order}: browser download failed for '{doc_name}': {browser_exc}",
                                flush=True,
                            )
                    else:
                        errors.append(str(exc))
                        print(
                            f"[error] {target.manifest.order}: direct download failed for '{doc_name}': {exc}",
                            flush=True,
                        )

            print(f"[ok] {target.manifest.order}: saved {saved} files", flush=True)
            valid_count = _count_valid_downloaded_files(folder)
            if valid_count > 0:
                status_rows.append(_status_row(target, "downloaded", "", valid_count))
            else:
                status_rows.append(_status_row(target, _classify_download_status(errors), _compress_errors(errors), 0))
    finally:
        if browser_bundle is not None:
            playwright, context = browser_bundle
            context.close()
            playwright.stop()
    _write_download_status(prepared_root / "_download_status.csv", status_rows)


def _build_targets(manifest_rows: list[ManifestRow]) -> list[MappingTarget]:
    targets: list[MappingTarget] = []
    for row in manifest_rows:
        url = row.url.strip()
        parsed = _extract_pro_url_parts(url)
        if parsed is None:
            continue
        pro_tender_id, pro_report_id = parsed
        report_id = PRO_REPORT_TO_API_REPORT.get(pro_report_id)
        if report_id is None:
            continue
        targets.append(
            MappingTarget(
                manifest=row,
                report_id=report_id,
                source_report_id=pro_report_id,
                pro_tender_id=pro_tender_id,
                external_tender_id=row.tender_id,
            )
        )
    return targets


def _load_manifest(path: Path) -> list[ManifestRow]:
    if not path.exists():
        raise SystemExit(f"Manifest not found: {path}")

    rows: list[ManifestRow] = []
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            order = str(row.get("order", "")).strip()
            tender_id = str(row.get("tender_id", "")).strip()
            title = str(row.get("title", "")).strip()
            folder_name = str(row.get("folder_name", "")).strip()
            url = str(row.get("url", "")).strip()
            report_id = str(row.get("report_id", "")).strip()
            api_seldon_id = str(row.get("api_seldon_id", "")).strip()
            if order and tender_id and folder_name:
                rows.append(
                    ManifestRow(
                        order=order,
                        tender_id=tender_id,
                        title=title,
                        folder_name=folder_name,
                        url=url,
                        report_id=report_id,
                        api_seldon_id=api_seldon_id,
                    )
                )
    return rows


def _extract_pro_url_parts(url: str) -> tuple[str, int] | None:
    match = re.search(r"/tender/(\d+)-(\d+)", url)
    if not match:
        return None
    return match.group(1), int(match.group(2))


def _select_filters_by_report(filters: list[dict[str, Any]]) -> dict[int, int]:
    selected: dict[int, int] = {}
    for item in filters:
        try:
            report_id = int(item.get("reportId"))
            filter_id = int(item.get("filterId"))
        except Exception:
            continue
        selected.setdefault(report_id, filter_id)
    return selected


def _collect_mappings_for_report(
    client: SeldonApiClient,
    filter_id: int,
    report_id: int,
    needed: dict[str, MappingTarget],
) -> dict[str, tuple[int, int]]:
    task_id = _create_task(client, filter_id)
    _wait_for_task(client, task_id)

    found: dict[str, tuple[int, int]] = {}
    for page_index in range(1, RESULTS_PAGE_LIMIT + 1):
        payload = client.request_json(
            "/Purchases/Result",
            json_body={"taskId": task_id, "pageIndex": page_index},
        )
        purchases = _extract_purchases(payload)
        if not purchases:
            break
        for purchase in purchases:
            try:
                seldon_id = int(purchase.get("SeldonId"))
            except Exception:
                continue
            purchase_report_id = int(purchase.get("reportId") or report_id)
            purchase_subject = str(purchase.get("subject") or "").strip()
            notification_number = str(purchase.get("notificationNumber") or "").strip()
            for lot in purchase.get("lotsList") or []:
                lot_id = str(lot.get("id") or "").strip()
                if lot_id and lot_id in needed:
                    found[lot_id] = (purchase_report_id, seldon_id)
            for pro_id, target in needed.items():
                if pro_id in found:
                    continue
                if notification_number and notification_number == target.external_tender_id:
                    if _subject_matches(target.manifest.title, purchase_subject):
                        found[pro_id] = (purchase_report_id, seldon_id)
        if all(pro_id in found for pro_id in needed):
            break
    return found


def _create_task(client: SeldonApiClient, filter_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "filterId": filter_id,
        "dateFrom": (now - timedelta(days=30)).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "dateTo": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }
    response = client.request_json("/Purchases/New", json_body=payload)
    task_id = str((((response or {}).get("result") or {}).get("taskId")) or "").strip()
    if not task_id:
        raise SeldonApiError(f"Purchases/New did not return taskId: {json.dumps(response, ensure_ascii=False)}")
    return task_id


def _wait_for_task(client: SeldonApiClient, task_id: str) -> None:
    for _ in range(POLL_ATTEMPTS):
        payload = client.request_json("/Purchases/Status", json_body={"taskId": task_id})
        result = ((payload or {}).get("result") or {})
        search_status = (result.get("searchStatus") or {}) if isinstance(result, dict) else {}
        code = search_status.get("code")
        if code == 3 or str(code) == "3":
            return
        time.sleep(POLL_SECONDS)
    raise SeldonApiError(f"Task did not become ready in time: {task_id}")


def _extract_purchases(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = ((payload or {}).get("result") or {})
    purchases = result.get("purchases")
    if isinstance(purchases, list):
        return [item for item in purchases if isinstance(item, dict)]
    return []


def _resolve_by_external_tender_id(
    client: SeldonApiClient,
    target: MappingTarget,
) -> tuple[int, int] | None:
    external_tender_id = str(target.external_tender_id or "").strip()
    if not external_tender_id:
        return None
    report_candidates: list[int] = []
    for candidate in (target.source_report_id, target.report_id):
        if candidate not in report_candidates:
            report_candidates.append(candidate)
    for report_id_candidate in report_candidates:
        try:
            payload = client.request_json(
                "/Purchases/Get",
                json_body={
                    "reportId": report_id_candidate,
                    "etpId": external_tender_id,
                },
            )
        except SeldonApiError:
            continue
        purchases = _extract_purchases(payload)
        if not purchases:
            continue
        purchase = purchases[0]
        try:
            seldon_id = int(purchase.get("SeldonId"))
        except Exception:
            continue
        report_id = int(purchase.get("reportId") or target.report_id)
        return report_id, seldon_id
    return None


def _resolve_from_manifest(manifest: ManifestRow) -> tuple[int, int] | None:
    if not manifest.report_id or not manifest.api_seldon_id:
        return None
    try:
        return int(manifest.report_id), int(manifest.api_seldon_id)
    except ValueError:
        return None


def _subject_matches(left: str, right: str) -> bool:
    left_norm = _normalize_text(left)
    right_norm = _normalize_text(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    shorter, longer = sorted((left_norm, right_norm), key=len)
    if len(shorter) < 40:
        return False
    return shorter in longer


def _normalize_text(value: str) -> str:
    normalized = value.casefold()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"[\"'`«»„“”]", "", normalized)
    return normalized.strip()


def _extract_documents(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = ((payload or {}).get("result") or {})
    groups = result.get("purchasesdocuments")
    if not isinstance(groups, list):
        return []
    for group in groups:
        if not isinstance(group, dict):
            continue
        documents = group.get("documents")
        if isinstance(documents, list):
            return [item for item in documents if isinstance(item, dict)]
    return []


def _download_document(url: str, output_path: Path) -> None:
    request = Request(
        url=url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
        },
    )
    try:
        status, content_type, body = _fetch_url(request)
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Connection error: {exc}") from exc

    if status >= 400:
        raise RuntimeError(f"HTTP {status}")
    if "text/html" in content_type and not _looks_like_binary(body):
        raise RuntimeError(f"HTML response instead of file ({content_type})")
    _raise_if_error_payload(body, content_type, output_path.name)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(body)


def _fetch_url(request: Request) -> tuple[int, str, bytes]:
    try:
        with urlopen(request, timeout=DIRECT_DOWNLOAD_TIMEOUT_SECONDS) as response:
            return (
                getattr(response, "status", 200),
                str(response.headers.get("Content-Type") or "").lower(),
                response.read(),
            )
    except URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError):
            insecure_context = ssl.create_default_context()
            insecure_context.check_hostname = False
            insecure_context.verify_mode = ssl.CERT_NONE
            with urlopen(request, timeout=DIRECT_DOWNLOAD_TIMEOUT_SECONDS, context=insecure_context) as response:
                return (
                    getattr(response, "status", 200),
                    str(response.headers.get("Content-Type") or "").lower(),
                    response.read(),
                )
        raise


def _start_browser(base_dir: Path, settings: Settings) -> tuple[Any, BrowserContext]:
    profile_dir = base_dir / "chrome_profiles" / "seldon_api_docs"
    profile_dir.mkdir(parents=True, exist_ok=True)
    playwright = sync_playwright().start()
    launch_errors: list[str] = []

    launch_attempts: list[dict[str, Any]] = [
        {
            "user_data_dir": str(profile_dir),
            "headless": settings.playwright_headless,
            "accept_downloads": True,
        }
    ]
    if sys.platform == "darwin":
        launch_attempts.insert(
            0,
            {
                "user_data_dir": str(profile_dir),
                "channel": "chrome",
                "headless": False,
                "accept_downloads": True,
            },
        )
        launch_attempts.append(
            {
                "user_data_dir": str(profile_dir),
                "headless": False,
                "accept_downloads": True,
            }
        )

    for kwargs in launch_attempts:
        try:
            context = playwright.chromium.launch_persistent_context(**kwargs)
            context.set_default_timeout(30000)
            context.set_default_navigation_timeout(90000)
            return playwright, context
        except Exception as exc:
            launch_errors.append(f"{kwargs}: {exc}")

    playwright.stop()
    raise RuntimeError("browser launch failed: " + " | ".join(launch_errors))


def _download_with_browser(context: BrowserContext, url: str, output_path: Path) -> None:
    host = urlparse(url).netloc.lower()
    page = context.new_page()
    try:
        if "gazprom-neft.ru" in host:
            if _download_on_navigation(page, url, output_path):
                return
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Error:
            pass

        if _click_and_save_download(page, output_path):
            return

        raise RuntimeError("download button was not found on source page")
    finally:
        page.close()


def _download_on_navigation(page: Page, url: str, output_path: Path) -> bool:
    try:
        with page.expect_download(timeout=90000) as download_info:
            page.goto(url, wait_until="commit", timeout=90000)
        _save_download(download_info.value, output_path)
        return True
    except Exception:
        return False


def _click_and_save_download(page: Page, output_path: Path) -> bool:
    button_patterns = [
        re.compile(r"скачать", re.IGNORECASE),
        re.compile(r"download", re.IGNORECASE),
    ]
    for pattern in button_patterns:
        for locator in (
            page.get_by_role("button", name=pattern),
            page.get_by_role("link", name=pattern),
            page.locator("text=СКАЧАТЬ"),
            page.locator("a:has-text('Скачать')"),
            page.locator("button:has-text('Скачать')"),
        ):
            try:
                if locator.count() == 0:
                    continue
                with page.expect_download(timeout=15000) as download_info:
                    locator.first.click()
                _save_download(download_info.value, output_path)
                return True
            except Exception:
                continue
    return False


def _save_download(download: Download, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".part")
    download.save_as(str(tmp_path))
    _raise_if_error_payload(tmp_path.read_bytes(), "", output_path.name)
    if output_path.exists():
        output_path.unlink()
    tmp_path.rename(output_path)


def _looks_like_binary(body: bytes) -> bool:
    if not body:
        return False
    head = body[:256]
    if b"\x00" in head:
        return True
    non_text = sum(1 for byte in head if byte < 9 or (13 < byte < 32))
    return non_text > max(3, len(head) // 10)


def _has_downloaded_files(folder: Path) -> bool:
    if not folder.exists():
        return False
    return any(
        path.is_file() and not path.name.startswith(".") and _is_valid_downloaded_file(path)
        for path in folder.rglob("*")
    )


def _count_valid_downloaded_files(folder: Path) -> int:
    if not folder.exists():
        return 0
    return sum(1 for path in folder.rglob("*") if _is_valid_downloaded_file(path))


def _status_row(target: MappingTarget, status: str, reason: str, valid_files: int) -> dict[str, str]:
    return {
        "order": target.manifest.order,
        "tender_id": target.manifest.tender_id,
        "title": target.manifest.title,
        "folder_name": target.manifest.folder_name,
        "status": status,
        "reason": reason,
        "valid_files": str(valid_files),
    }


def _compress_errors(errors: list[str]) -> str:
    unique: list[str] = []
    for error in errors:
        value = error.strip()
        if value and value not in unique:
            unique.append(value)
    return " | ".join(unique[:3])


def _classify_download_status(errors: list[str]) -> str:
    normalized = [error.strip().lower() for error in errors if error.strip()]
    if not normalized:
        return "download_failed"
    if all("доступ к файлу запрещен" in error or "status=403" in error for error in normalized):
        return "access_denied"
    if all("download button was not found on source page" in error for error in normalized):
        return "manual_only"
    return "download_failed"


def _write_download_status(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = ["order", "tender_id", "title", "folder_name", "status", "reason", "valid_files"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _is_valid_downloaded_file(path: Path) -> bool:
    if not path.is_file() or path.name.startswith("."):
        return False
    try:
        body = path.read_bytes()
    except Exception:
        return False
    if not body:
        return False
    try:
        _raise_if_error_payload(body, "", path.name)
    except RuntimeError:
        return False
    return True


def _raise_if_error_payload(body: bytes, content_type: str, filename: str) -> None:
    stripped = body.lstrip()
    looks_like_json = "json" in content_type or stripped.startswith(b"{") or stripped.startswith(b"[")
    if not looks_like_json:
        return
    try:
        payload = json.loads(body.decode("utf-8", errors="ignore"))
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    error = payload.get("error")
    if isinstance(error, dict):
        status_code = error.get("statusCode")
        message = str(error.get("message") or "").strip()
        if status_code or message:
            raise RuntimeError(
                f"error payload instead of file for '{filename}': status={status_code or '?'} message={message or 'unknown'}"
            )
    status = payload.get("status")
    if isinstance(status, dict):
        code = status.get("code")
        descr = str(status.get("descr") or "").strip()
        if code and int(code) >= 400:
            raise RuntimeError(
                f"error payload instead of file for '{filename}': status={code} message={descr or 'unknown'}"
            )


def _sanitize_filename(value: str) -> str:
    sanitized = (
        value.replace("/", "_")
        .replace("\\", "_")
        .replace("\0", "")
        .strip()
        .rstrip(". ")
    )
    sanitized = re.sub(r"[\r\n\t]+", " ", sanitized)
    sanitized = re.sub(r"\s{2,}", " ", sanitized)
    if not sanitized:
        sanitized = "document"
    raw = sanitized.encode("utf-8")
    if len(raw) <= 180:
        return sanitized
    trimmed = raw[:177]
    while trimmed:
        try:
            return trimmed.decode("utf-8").rstrip() + "..."
        except UnicodeDecodeError:
            trimmed = trimmed[:-1]
    return "document"


if __name__ == "__main__":
    main()
