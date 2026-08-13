from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from tender_agent.config import Settings
from tender_agent.excel_loader import load_tenders
from tender_agent.models import TenderRow
from tender_agent.platforms import DocumentAccessBlockedError, SeldonFirstAdapter


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(
            "Usage: .venv/bin/python download_seldon_with_chrome.py /path/to/prepared_folders /path/to/export.xls"
        )

    base_dir = Path(__file__).resolve().parent
    settings = Settings.load(base_dir)
    prepared_root = Path(sys.argv[1]).expanduser().resolve()
    xls_path = Path(sys.argv[2]).expanduser().resolve()
    profile_dir = base_dir / "chrome_profiles" / "seldon"

    if not prepared_root.exists():
        raise SystemExit(f"Prepared folders directory not found: {prepared_root}")
    if not xls_path.exists():
        raise SystemExit(f"XLS file not found: {xls_path}")

    adapter = SeldonFirstAdapter(
        login_url=settings.platform_login_url,
        username=settings.platform_username,
        password=settings.platform_password,
        selectors_path=settings.selector_config.config_path,
    )
    tenders = load_tenders(
        xls_path=xls_path,
        url_column=settings.platform_tender_url_column,
        id_column=settings.platform_tender_id_column,
        title_column=settings.platform_tender_title_column,
    )
    tender_map = {t.tender_id: t for t in tenders}
    manifest = _load_manifest(prepared_root / "_manifest.csv")

    profile_dir.mkdir(parents=True, exist_ok=True)
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
            _ensure_login(page, adapter, settings.platform_base_url or "https://pro.myseldon.com/ru/")
            page.close()

            for order, tender_id, folder_name in manifest:
                tender = tender_map.get(tender_id)
                if tender is None:
                    print(f"[skip] {order}: tender_id={tender_id} not found in XLS", flush=True)
                    continue

                target_dir = prepared_root / folder_name
                if _has_downloaded_files(target_dir):
                    print(f"[skip] {order}: {folder_name} already has files", flush=True)
                    continue

                print(f"[run] {order}: {tender.title}", flush=True)
                try:
                    downloaded = adapter.download_documents(context, tender, target_dir)
                    print(f"[ok] {order}: saved {len(downloaded.files)} files", flush=True)
                except DocumentAccessBlockedError as exc:
                    print(f"[blocked] {order}: {exc}", flush=True)
                except Exception as exc:
                    print(f"[error] {order}: {exc}", flush=True)
                time.sleep(1.0)
        finally:
            context.close()


def _ensure_login(page, adapter: SeldonFirstAdapter, base_url: str) -> None:
    page.goto(base_url, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    if page.locator("text=Войти").count():
        adapter.login(page)


def _load_manifest(path: Path) -> list[tuple[str, str, str]]:
    if not path.exists():
        raise SystemExit(f"Manifest not found: {path}")

    rows: list[tuple[str, str, str]] = []
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            order = str(row.get("order", "")).strip()
            tender_id = str(row.get("tender_id", "")).strip()
            folder_name = str(row.get("folder_name", "")).strip()
            if order and tender_id and folder_name:
                rows.append((order, tender_id, folder_name))
    return rows


def _has_downloaded_files(folder: Path) -> bool:
    if not folder.exists():
        return False
    for path in folder.rglob("*"):
        if path.is_file() and not path.name.startswith("."):
            return True
    return False


if __name__ == "__main__":
    main()
