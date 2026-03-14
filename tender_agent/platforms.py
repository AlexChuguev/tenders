from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError

from tender_agent.models import DownloadedTender, TenderRow


ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".rtf", ".txt"}
SELDON_HOSTS = {"pro.myseldon.com", "myseldon.com"}
IGNORED_HOST_SUBSTRINGS = {"t.me", "telegram.me", "vk.cc", "youtube.com", "youtu.be"}


class DocumentAccessBlockedError(RuntimeError):
    """The tender page exposes documents, but the source blocks automated download."""


@dataclass
class PublicDocumentAdapter:
    selectors_path: Path

    def download_documents(
        self,
        context: BrowserContext,
        tender: TenderRow,
        target_dir: Path,
    ) -> DownloadedTender:
        selectors = self._load_selectors()
        documents_cfg = selectors["documents"]
        page = context.new_page()
        page.goto(tender.url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        if documents_cfg.get("container"):
            try:
                page.wait_for_selector(documents_cfg["container"], timeout=10000)
            except Exception:
                pass

        hrefs = self._collect_hrefs(page, documents_cfg.get("links", []))
        if not hrefs:
            hrefs = self._collect_hrefs(page, ["a"])
        downloaded = _download_from_hrefs(page, tender, target_dir, hrefs)
        page.close()
        if not downloaded.files:
            raise RuntimeError("Documents were not found on the external source page")
        return downloaded

    def _collect_hrefs(self, page: Page, selectors: list[str]) -> list[tuple[str, str]]:
        hrefs = []
        for css in selectors:
            for link in page.locator(css).all():
                href = link.get_attribute("href")
                if href:
                    text = ""
                    try:
                        text = link.inner_text(timeout=500)
                    except Exception:
                        pass
            absolute_href = urljoin(page.url, href)
            if _is_supported_scheme(absolute_href) and not _is_ignored_host(absolute_href):
                hrefs.append((absolute_href, text))
        return hrefs

    def _load_selectors(self) -> dict:
        if not self.selectors_path.exists():
            return {"documents": {"container": "body", "links": ["a"]}}
        return json.loads(self.selectors_path.read_text(encoding="utf-8"))


@dataclass
class SeldonFirstAdapter:
    login_url: str
    username: str
    password: str
    selectors_path: Path

    def __post_init__(self) -> None:
        self.fallback = PublicDocumentAdapter(selectors_path=self.selectors_path)

    def login(self, page: Page) -> None:
        page.goto(self.login_url, wait_until="domcontentloaded")
        page.fill("#Email", self.username)
        page.fill("#Password", self.password)
        page.click("button[type='submit']")
        page.wait_for_url("https://pro.myseldon.com/ru/", timeout=30000)

    def download_documents(
        self,
        context: BrowserContext,
        tender: TenderRow,
        target_dir: Path,
    ) -> DownloadedTender:
        page = context.new_page()
        page.goto(tender.url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        seldon_hrefs = self._collect_document_hrefs(page)
        if seldon_hrefs:
            downloaded = _download_from_hrefs(page, tender, target_dir, seldon_hrefs)
            page.close()
            if not downloaded.files:
                raise DocumentAccessBlockedError(
                    "Документы видны в карточке, но скачивание заблокировано антибот-защитой площадки."
                )
            return downloaded

        external_url = self._find_external_source_url(page)
        page.close()
        if not external_url:
            raise RuntimeError("Documents were not found in Seldon and no external source link was detected")

        external_tender = TenderRow(
            row_number=tender.row_number,
            tender_id=tender.tender_id,
            title=tender.title,
            url=external_url,
            deadline_at=tender.deadline_at,
            customer=tender.customer,
            customer_inn=tender.customer_inn,
            raw=tender.raw,
        )
        return self.fallback.download_documents(context, external_tender, target_dir)

    def _collect_document_hrefs(self, page: Page) -> list[tuple[str, str]]:
        hrefs: dict[str, str] = {}
        for link in page.locator("a").all():
            href = link.get_attribute("href")
            if not href:
                continue
            absolute = urljoin(page.url, href)
            if not _is_supported_scheme(absolute) or _is_ignored_host(absolute):
                continue
            text = ""
            try:
                text = link.inner_text(timeout=500)
            except Exception:
                pass
            if _is_document_link(absolute, text):
                hrefs[absolute] = text
        return list(hrefs.items())

    def _find_external_source_url(self, page: Page) -> str:
        candidates: list[str] = []
        for link in page.locator("a").all():
            href = link.get_attribute("href")
            if not href:
                continue
            absolute = urljoin(page.url, href)
            if not _is_supported_scheme(absolute) or _is_ignored_host(absolute):
                continue
            host = urlparse(absolute).netloc.lower()
            if not host or host in SELDON_HOSTS:
                continue
            if "basis.myseldon.com" in host:
                continue
            candidates.append(absolute)

        for candidate in candidates:
            if any(token in candidate.lower() for token in ["tender", "zakup", "purchase", "process", "trade"]):
                return candidate
        return ""


def _download_from_hrefs(
    page: Page,
    tender: TenderRow,
    target_dir: Path,
    hrefs: list[tuple[str, str]],
) -> DownloadedTender:
    unique_hrefs: dict[str, str] = {}
    for href, text in hrefs:
        unique_hrefs[href] = _detect_filename(href, text)

    downloaded = DownloadedTender(tender=tender, directory=target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    for href, fallback_filename in unique_hrefs.items():
        try:
            locator = page.locator(f"a[href='{href}']").first
            download = None
            try:
                with page.expect_download(timeout=10000) as download_info:
                    if locator.count():
                        locator.click()
                    else:
                        page.goto(href, wait_until="domcontentloaded")
                download = download_info.value
            except Exception as exc:
                if not _looks_like_download_start(exc):
                    raise

            if download is not None:
                filename = download.suggested_filename or fallback_filename
                output_path = target_dir / filename
                download.save_as(str(output_path))
                downloaded.files.append(output_path)
                try:
                    page.go_back(wait_until="domcontentloaded", timeout=10000)
                except Exception:
                    pass
                continue
        except (PlaywrightTimeoutError, Exception):
            response = page.context.request.get(href, timeout=30000)
            if not response.ok:
                continue
            filename = _filename_from_headers(response.headers, fallback_filename)
            if _is_html_response(response.headers, filename):
                continue
            output_path = target_dir / filename
            output_path.write_bytes(response.body())
            downloaded.files.append(output_path)
    return downloaded


def _detect_filename(href: str, text: str) -> str:
    cleaned_text = re.sub(r"[^a-zA-Z0-9а-яА-Я._-]+", "_", (text or "").strip()).strip("_")
    from_href = href.rsplit("/", 1)[-1].split("?", 1)[0]
    candidate = from_href or cleaned_text or "document"
    if "." not in candidate and cleaned_text:
        candidate = f"{cleaned_text}.bin"
    return candidate[:180]


def _is_document_link(href: str, text: str) -> bool:
    parsed = urlparse(href)
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.fragment and not parsed.path:
        return False
    path = urlparse(href).path.lower()
    ext = Path(path).suffix.lower()
    if ext in ALLOWED_EXTENSIONS:
        return True
    text_lower = (text or "").lower()
    href_lower = href.lower()
    return any(token in href_lower or token in text_lower for token in ["скач", "влож", "download", "file"])


def _filename_from_headers(headers: dict[str, str], fallback: str) -> str:
    content_disposition = headers.get("content-disposition", "")
    match = re.search(r'filename="?([^";]+)"?', content_disposition, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return fallback


def _is_html_response(headers: dict[str, str], filename: str) -> bool:
    content_type = headers.get("content-type", "").lower()
    return "text/html" in content_type and Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS


def _looks_like_download_start(exc: Exception) -> bool:
    message = str(exc).lower()
    return "download is starting" in message


def _is_supported_scheme(href: str) -> bool:
    return urlparse(href).scheme in {"http", "https"}


def _is_ignored_host(href: str) -> bool:
    host = urlparse(href).netloc.lower()
    return any(token in host for token in IGNORED_HOST_SUBSTRINGS)
