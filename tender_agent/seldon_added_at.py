from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

try:
    from playwright.sync_api import BrowserContext, sync_playwright
except Exception:  # pragma: no cover - optional dependency in local mode
    BrowserContext = Any  # type: ignore[assignment]
    sync_playwright = None

from tender_agent.config import Settings
from tender_agent.platforms import SeldonFirstAdapter


SELDON_HOSTS = {"pro.myseldon.com", "myseldon.com"}

_DATE_PATTERNS = [
    re.compile(
        r"(?:дата\s+добавления(?:\s+в\s+систему)?|добавлен[ао]?\s+в\s+систему)\s*[:\-]?\s*"
        r"(\d{1,2}\.\d{1,2}\.\d{4}(?:\s+\d{1,2}:\d{2})?)",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"(?:дата\s+публикации|опубликован[ао]?)\s*[:\-]?\s*"
        r"(\d{1,2}\.\d{1,2}\.\d{4}(?:\s+\d{1,2}:\d{2})?)",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"(?:лот|тендер|закупк[аи])[^.\n]{0,160}?размещен[ао]?\s*"
        r"(\d{1,2}\.\d{1,2}\.\d{4}(?:\s+\d{1,2}:\d{2})?)",
        flags=re.IGNORECASE,
    ),
]


class NullSeldonAddedAtResolver:
    def __enter__(self) -> "NullSeldonAddedAtResolver":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def get(self, url: str) -> datetime | None:
        return None


def should_resolve_seldon_added_at(settings: Settings, urls: Iterable[str]) -> bool:
    if not settings.resolve_seldon_added_at:
        return False
    if sync_playwright is None:
        return False
    if not settings.platform_login_url or not settings.platform_username or not settings.platform_password:
        return False
    if not settings.selector_config.config_path.exists():
        return False
    return any(_is_seldon_url(str(url).strip()) for url in urls if str(url).strip())


class SeldonAddedAtResolver:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._playwright = None
        self._browser = None
        self._context: BrowserContext | None = None
        self._cache: dict[str, datetime | None] = {}

    def __enter__(self) -> "SeldonAddedAtResolver":
        if sync_playwright is None:
            return self
        if not self.settings.platform_login_url or not self.settings.platform_username or not self.settings.platform_password:
            return self
        if not self.settings.selector_config.config_path.exists():
            return self
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self.settings.playwright_headless)
            self._context = self._browser.new_context()
            page = self._context.new_page()
            adapter = SeldonFirstAdapter(
                login_url=self.settings.platform_login_url,
                username=self.settings.platform_username,
                password=self.settings.platform_password,
                selectors_path=self.settings.selector_config.config_path,
            )
            adapter.login(page)
            page.close()
        except Exception:
            self._context = None
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._context is not None:
            self._context.close()
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()

    def get(self, url: str) -> datetime | None:
        normalized_url = url.strip()
        if not normalized_url:
            return None
        if normalized_url in self._cache:
            return self._cache[normalized_url]
        if not _is_seldon_url(normalized_url):
            self._cache[normalized_url] = None
            return None
        if self._context is None:
            self._cache[normalized_url] = None
            return None

        page = self._context.new_page()
        page.set_default_timeout(15000)
        page.set_default_navigation_timeout(20000)
        try:
            page.goto(normalized_url, wait_until="domcontentloaded", timeout=20000)
            try:
                page.wait_for_load_state("networkidle", timeout=7000)
            except Exception:
                pass
            body_text = page.locator("body").inner_text(timeout=5000)
            resolved = _extract_seldon_added_at(body_text)
        except Exception:
            resolved = None
        finally:
            page.close()
        self._cache[normalized_url] = resolved
        return resolved


def _is_seldon_url(url: str) -> bool:
    return urlparse(url).netloc.lower() in SELDON_HOSTS


def _extract_seldon_added_at(body_text: str) -> datetime | None:
    normalized = body_text.replace("\xa0", " ")
    for pattern in _DATE_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        raw = match.group(1).strip()
        for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
    return None
