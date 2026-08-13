from __future__ import annotations

import csv
import os
import random
import sys
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from tender_agent.config import Settings
from tender_agent.models import TenderRow
from tender_agent.platforms import DocumentAccessBlockedError, PublicDocumentAdapter, SeldonFirstAdapter


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage: .venv/bin/python download_chrome_from_manifest.py /path/to/prepared_folders"
        )

    base_dir = Path(__file__).resolve().parent
    settings = Settings.load(base_dir)
    prepared_root = Path(sys.argv[1]).expanduser().resolve()
    profile_dir = base_dir / "chrome_profiles" / "seldon"

    if not prepared_root.exists():
        raise SystemExit(f"Prepared folders directory not found: {prepared_root}")

    manifest = _load_manifest(prepared_root / "_manifest.csv")
    profile_dir.mkdir(parents=True, exist_ok=True)
    limits = _load_limits()
    filters = _load_filters()

    seldon_adapter = SeldonFirstAdapter(
        login_url=settings.platform_login_url,
        username=settings.platform_username,
        password=settings.platform_password,
        selectors_path=settings.selector_config.config_path,
    )
    public_adapter = PublicDocumentAdapter(selectors_path=settings.selector_config.config_path)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel="chrome",
            headless=False,
            accept_downloads=True,
        )
        context.set_default_timeout(15000)
        context.set_default_navigation_timeout(20000)
        try:
            page = context.new_page()
            _ensure_seldon_login(page, seldon_adapter, settings.platform_base_url or "https://pro.myseldon.com/ru/")
            page.close()

            processed_total = 0
            downloaded_files_total = 0
            processed_by_host: dict[str, int] = defaultdict(int)
            files_by_host: dict[str, int] = defaultdict(int)
            blocked_by_host: dict[str, int] = defaultdict(int)
            errors_by_host: dict[str, int] = defaultdict(int)
            disabled_hosts: set[str] = set()

            for row in manifest:
                host = (row.get("platform_host") or _host_from_url(row.get("url", "")) or "unknown").lower()
                if filters["only_hosts"] and host not in filters["only_hosts"]:
                    continue
                target_dir = prepared_root / row["folder_name"]
                if _has_downloaded_files(target_dir):
                    print(f"[skip] {row['order']}: {row['folder_name']} already has files", flush=True)
                    continue

                if not row["url"]:
                    print(f"[skip] {row['order']}: no URL in manifest", flush=True)
                    continue

                if row.get("download_strategy") in {"manual", "network_blocked"}:
                    print(
                        f"[{row.get('download_strategy')}] {row['order']}: {row['title']} [{row.get('platform_host') or 'unknown host'}]",
                        flush=True,
                    )
                    continue

                if processed_total >= limits["max_tenders_total"]:
                    print(f"[stop] global tender limit reached: {limits['max_tenders_total']}", flush=True)
                    break

                if downloaded_files_total >= limits["max_files_total"]:
                    print(f"[stop] global file limit reached: {limits['max_files_total']}", flush=True)
                    break

                if host in disabled_hosts:
                    print(f"[skip] {row['order']}: host paused after repeated failures [{host}]", flush=True)
                    continue

                if processed_by_host[host] >= limits["max_tenders_per_host"]:
                    print(
                        f"[skip] {row['order']}: host tender limit reached [{host} / {limits['max_tenders_per_host']}]",
                        flush=True,
                    )
                    continue

                if files_by_host[host] >= limits["max_files_per_host"]:
                    print(
                        f"[skip] {row['order']}: host file limit reached [{host} / {limits['max_files_per_host']}]",
                        flush=True,
                    )
                    continue

                tender = TenderRow(
                    row_number=int(row["order"]),
                    tender_id=row["tender_id"],
                    title=row["title"],
                    url=row["url"],
                    deadline_at=None,
                    customer="",
                    customer_inn="",
                    raw=row,
                )

                adapter = seldon_adapter if row["download_mode"] == "seldon" else public_adapter
                print(
                    f"[run] {row['order']}: {row['title']} [{row['download_strategy']}/{row.get('platform_host') or 'unknown'}]",
                    flush=True,
                )
                processed_total += 1
                processed_by_host[host] += 1
                try:
                    downloaded = adapter.download_documents(context, tender, target_dir)
                    new_files = len(downloaded.files)
                    downloaded_files_total += new_files
                    files_by_host[host] += new_files
                    print(f"[ok] {row['order']}: saved {len(downloaded.files)} files", flush=True)
                except DocumentAccessBlockedError as exc:
                    blocked_by_host[host] += 1
                    print(f"[blocked] {row['order']}: {exc}", flush=True)
                    if blocked_by_host[host] >= limits["max_blocked_per_host"]:
                        disabled_hosts.add(host)
                        print(f"[pause-host] too many blocked downloads on {host}", flush=True)
                except Exception as exc:
                    errors_by_host[host] += 1
                    print(f"[error] {row['order']}: {exc}", flush=True)
                    if errors_by_host[host] >= limits["max_errors_per_host"]:
                        disabled_hosts.add(host)
                        print(f"[pause-host] too many errors on {host}", flush=True)
                _sleep_between(limits["pause_min_seconds"], limits["pause_max_seconds"])
        finally:
            context.close()


def _ensure_seldon_login(page, adapter: SeldonFirstAdapter, base_url: str) -> None:
    page.goto(base_url, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    if page.locator("text=Войти").count():
        adapter.login(page)


def _load_manifest(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(f"Manifest not found: {path}")

    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return [{key: str(value or "").strip() for key, value in row.items()} for row in reader]


def _has_downloaded_files(folder: Path) -> bool:
    if not folder.exists():
        return False
    for path in folder.rglob("*"):
        if path.is_file() and not path.name.startswith("."):
            return True
    return False


def _load_limits() -> dict[str, int | float]:
    return {
        "pause_min_seconds": _float_env("DOWNLOAD_PAUSE_MIN_SECONDS", 2.0),
        "pause_max_seconds": _float_env("DOWNLOAD_PAUSE_MAX_SECONDS", 4.5),
        "max_tenders_total": _int_env("DOWNLOAD_MAX_TENDERS_TOTAL", 12),
        "max_tenders_per_host": _int_env("DOWNLOAD_MAX_TENDERS_PER_HOST", 4),
        "max_files_total": _int_env("DOWNLOAD_MAX_FILES_TOTAL", 40),
        "max_files_per_host": _int_env("DOWNLOAD_MAX_FILES_PER_HOST", 12),
        "max_blocked_per_host": _int_env("DOWNLOAD_MAX_BLOCKED_PER_HOST", 2),
        "max_errors_per_host": _int_env("DOWNLOAD_MAX_ERRORS_PER_HOST", 2),
    }


def _load_filters() -> dict[str, set[str]]:
    return {
        "only_hosts": _csv_env_set("DOWNLOAD_ONLY_HOSTS"),
    }


def _sleep_between(min_seconds: float, max_seconds: float) -> None:
    time.sleep(random.uniform(min_seconds, max_seconds))


def _host_from_url(url: str) -> str:
    return urlparse(url).netloc.lower()


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    return int(value)


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    return float(value)


def _csv_env_set(name: str) -> set[str]:
    value = os.getenv(name, "").strip()
    if not value:
        return set()
    return {part.strip().lower() for part in value.split(",") if part.strip()}


if __name__ == "__main__":
    main()
