from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class BrowserSelectors:
    config_path: Path


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    llm_api_key: str
    llm_model: str
    llm_base_url: str
    max_files_per_tender: int
    input_xls: Path
    download_dir: Path
    output_xlsx: Path
    platform_name: str
    platform_base_url: str
    platform_login_url: str
    platform_username: str
    platform_password: str
    platform_tender_url_column: str
    platform_tender_id_column: str
    platform_tender_title_column: str
    playwright_headless: bool
    tender_skip: int
    tender_limit: int | None
    local_files_dir: Path
    review_target_date: str
    selector_config: BrowserSelectors
    prompt_template_path: Path

    @classmethod
    def load(cls, base_dir: Path) -> "Settings":
        _load_dotenv(base_dir / ".env")
        return cls(
            llm_provider=os.getenv("LLM_PROVIDER", "openai"),
            llm_api_key=os.getenv("LLM_API_KEY", os.getenv("OPENAI_API_KEY", "")).strip(),
            llm_model=os.getenv("LLM_MODEL", os.getenv("OPENAI_MODEL", "gpt-4.1")),
            llm_base_url=os.getenv("LLM_BASE_URL", "").strip(),
            max_files_per_tender=_int_env("MAX_FILES_PER_TENDER", default=4) or 4,
            input_xls=Path(_require_env("TENDER_INPUT_XLS")),
            download_dir=Path(_require_env("DOWNLOAD_DIR")),
            output_xlsx=Path(_require_env("OUTPUT_XLSX")),
            platform_name=os.getenv("PLATFORM_NAME", "generic"),
            platform_base_url=os.getenv("PLATFORM_BASE_URL", ""),
            platform_login_url=_require_env("PLATFORM_LOGIN_URL"),
            platform_username=_require_env("PLATFORM_USERNAME"),
            platform_password=_require_env("PLATFORM_PASSWORD"),
            platform_tender_url_column=os.getenv("PLATFORM_TENDER_URL_COLUMN", "Ссылка"),
            platform_tender_id_column=os.getenv("PLATFORM_TENDER_ID_COLUMN", "Номер"),
            platform_tender_title_column=os.getenv("PLATFORM_TENDER_TITLE_COLUMN", "Наименование"),
            playwright_headless=_bool_env("PLAYWRIGHT_HEADLESS", True),
            tender_skip=_int_env("TENDER_SKIP", default=0) or 0,
            tender_limit=_int_env("TENDER_LIMIT"),
            local_files_dir=Path(os.getenv("LOCAL_FILES_DIR", str(base_dir / "manual_downloads"))),
            review_target_date=os.getenv("REVIEW_TARGET_DATE", "yesterday"),
            selector_config=BrowserSelectors(
                config_path=Path(os.getenv("PLATFORM_SELECTOR_CONFIG", str(base_dir / "platform_selectors.json")))
            ),
            prompt_template_path=Path(os.getenv("PROMPT_TEMPLATE_PATH", str(base_dir / "prompt_template.md"))),
        )


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is required")
    return value


def _int_env(name: str, default: int | None = None) -> int | None:
    value = os.getenv(name)
    if not value:
        return default
    return int(value)
