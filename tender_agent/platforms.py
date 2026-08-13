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
DOWNLOAD_DEBUG_LOG = Path(__file__).resolve().parents[1] / "download_debug.log"


class DocumentAccessBlockedError(RuntimeError):
    """The tender page exposes documents, but the source blocks automated download."""


def _log_download_debug(message: str) -> None:
    try:
        DOWNLOAD_DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with DOWNLOAD_DEBUG_LOG.open("a", encoding="utf-8") as fh:
            fh.write(message.rstrip() + "\n")
    except Exception:
        pass


@dataclass
class PublicDocumentAdapter:
    selectors_path: Path

    def download_documents(
        self,
        context: BrowserContext,
        tender: TenderRow,
        target_dir: Path,
    ) -> DownloadedTender:
        host = urlparse(tender.url).netloc.lower()
        selectors = self._load_selectors()
        documents_cfg = self._select_documents_config(selectors, tender.url)
        page = context.new_page()
        page.set_default_timeout(15000)
        page.set_default_navigation_timeout(20000)
        page.goto(tender.url, wait_until="domcontentloaded", timeout=20000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        if documents_cfg.get("container"):
            try:
                page.wait_for_selector(documents_cfg["container"], timeout=10000)
            except Exception:
                pass

        if "sberbank-ast.ru" in host:
            downloaded = self._download_sberbank_ast_documents(page, tender, target_dir)
            page.close()
            if not downloaded.files:
                raise RuntimeError("Documents were not found on the Sberbank-AST source page")
            return downloaded

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
                if not href:
                    continue
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

    def _select_documents_config(self, selectors: dict, url: str) -> dict:
        host = urlparse(url).netloc.lower()
        host_overrides = selectors.get("documents_by_host", {})
        if isinstance(host_overrides, dict):
            for host_pattern, override in host_overrides.items():
                if host_pattern.lower() in host and isinstance(override, dict):
                    merged = dict(selectors.get("documents", {}))
                    merged.update(override)
                    return merged
        return selectors.get("documents", {"container": "body", "links": ["a"]})

    def _download_sberbank_ast_documents(
        self,
        page: Page,
        tender: TenderRow,
        target_dir: Path,
    ) -> DownloadedTender:
        entries = page.evaluate(
            """
            () => {
              const links = [...document.querySelectorAll("a[onclick*='OpenFile('], a[id*='txbFileDocsName'], a span[content='leaf:FileName']")];
              const normalized = links.map((node) => node.tagName === "SPAN" ? node.closest("a") : node).filter(Boolean);
              const unique = [...new Map(normalized.map((link) => [link.outerHTML, link])).values()];
              return unique.map((link) => {
                const row = link.closest("tr") || link.parentElement?.parentElement;
                const fileNode = row && typeof findXMLNodeByName === "function"
                  ? findXMLNodeByName(row, "FileID")
                  : null;
                const onclick = link.getAttribute("onclick") || "";
                const match = onclick.match(/OpenFile\\('([^']+)'\\s*,/);
                return {
                  name: (link.textContent || "").trim(),
                  tsCode: match ? match[1] : "",
                  fid: fileNode ? fileNode.value : ""
                };
              }).filter((item) => item.name && item.tsCode && item.fid);
            }
            """
        )
        _log_download_debug(f"[sberbank-ast] entries {tender.tender_id} count={len(entries)} url={page.url}")
        if not entries:
            return DownloadedTender(tender=tender, directory=target_dir)

        target_dir.mkdir(parents=True, exist_ok=True)
        downloaded = DownloadedTender(tender=tender, directory=target_dir)
        links = page.locator("a[onclick*='OpenFile(']")
        count = links.count()
        for index in range(count):
            link = links.nth(index)
            try:
                name = entries[index]["name"]
            except Exception:
                name = f"document_{index + 1}"
            try:
                with page.expect_download(timeout=15000) as download_info:
                    link.click()
                download = download_info.value
                suggested_filename = (download.suggested_filename or "").strip()
                filename = _choose_download_filename(suggested_filename, name, page.url)
                output_path = target_dir / filename
                download.save_as(str(output_path))
                downloaded.files.append(output_path)
                _log_download_debug(f"[sberbank-ast] saved {tender.tender_id} -> {output_path}")
                continue
            except Exception as exc:
                _log_download_debug(f"[sberbank-ast] click-download-failed {tender.tender_id} {name}: {exc}")

            entry = entries[index]
            href = urljoin(page.url, f"/{entry['tsCode']}/File/DownloadFile?fid={entry['fid']}")
            _log_download_debug(f"[sberbank-ast] request-fallback {tender.tender_id} {name} -> {href}")
            response = page.context.request.get(href, timeout=30000)
            _log_download_debug(
                f"[sberbank-ast] response-fallback {tender.tender_id} status={response.status} "
                f"content-type={response.headers.get('content-type','')} "
                f"content-disposition={response.headers.get('content-disposition','')}"
            )
            if not response.ok:
                continue
            fallback_name = _detect_filename(href, name)
            filename = _filename_from_headers(response.headers, fallback_name)
            if _is_html_response(response.headers, filename):
                _log_download_debug(f"[sberbank-ast] skip-html {tender.tender_id} {name} -> {filename}")
                continue
            output_path = target_dir / filename
            output_path.write_bytes(response.body())
            downloaded.files.append(output_path)
            _log_download_debug(f"[sberbank-ast] saved-fallback {tender.tender_id} -> {output_path}")
        return downloaded


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
        page.set_default_timeout(15000)
        page.set_default_navigation_timeout(20000)
        page.goto(tender.url, wait_until="domcontentloaded", timeout=20000)
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


def _choose_download_filename(suggested_filename: str, text: str, href: str) -> str:
    safe_text_name = _detect_filename(href, text)
    if not suggested_filename:
        return safe_text_name
    normalized = suggested_filename.strip()
    if _looks_broken_filename(normalized):
        return safe_text_name
    if "." not in normalized:
        return safe_text_name
    return normalized[:180]


def _looks_broken_filename(filename: str) -> bool:
    stripped = filename.strip()
    if not stripped:
        return True
    alpha_num_count = sum(char.isalnum() for char in stripped)
    if alpha_num_count < 4:
        return True
    if not any(char.isalpha() for char in stripped):
        return True
    return False


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
