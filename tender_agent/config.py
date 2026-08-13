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
    seldon_api_base_url: str
    seldon_api_login: str
    seldon_api_password: str
    seldon_api_timeout_seconds: int
    tenderplan_base_url: str
    tenderplan_pat: str
    tenderplan_timeout_seconds: int
    search_profile_path: Path
    max_files_per_tender: int
    analysis_max_chars_per_file: int
    input_xls: Path
    download_dir: Path
    output_xlsx: Path
    artifact_dir: Path
    triage_policy_path: Path
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
    review_mode: str
    deep_review_max_files_per_tender: int
    deep_review_max_chars_per_file: int
    resolve_seldon_added_at: bool
    llm_preflight_checks: int
    llm_preflight_required_successes: int
    tender_network_retries: int
    selector_config: BrowserSelectors
    prompt_template_path: Path

    @classmethod
    def load(cls, base_dir: Path) -> "Settings":
        _load_dotenv(base_dir / ".env")
        return cls(
            llm_provider=os.getenv("LLM_PROVIDER", "openai"),
            llm_api_key=os.getenv("LLM_API_KEY", os.getenv("OPENAI_API_KEY", "")).strip(),
            llm_model=os.getenv("LLM_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini")),
            llm_base_url=os.getenv("LLM_BASE_URL", "").strip(),
            seldon_api_base_url=os.getenv("SELDON_API_BASE_URL", "https://apitorgi.myseldon.com").strip(),
            seldon_api_login=os.getenv("SELDON_API_LOGIN", "").strip(),
            seldon_api_password=os.getenv("SELDON_API_PASSWORD", "").strip(),
            seldon_api_timeout_seconds=_int_env("SELDON_API_TIMEOUT_SECONDS", default=30) or 30,
            tenderplan_base_url=os.getenv("TENDERPLAN_BASE_URL", "https://tenderplan.ru").strip(),
            tenderplan_pat=os.getenv("TENDERPLAN_PAT", "").strip(),
            tenderplan_timeout_seconds=_int_env("TENDERPLAN_TIMEOUT_SECONDS", default=30) or 30,
            search_profile_path=Path(
                os.getenv("SEARCH_PROFILE_PATH", str(base_dir / "search_profile.flatsystems.json"))
            ),
            max_files_per_tender=_int_env("MAX_FILES_PER_TENDER", default=4) or 4,
            analysis_max_chars_per_file=_int_env("ANALYSIS_MAX_CHARS_PER_FILE", default=8000) or 8000,
            input_xls=Path(_require_env("TENDER_INPUT_XLS")),
            download_dir=Path(_require_env("DOWNLOAD_DIR")),
            output_xlsx=Path(_require_env("OUTPUT_XLSX")),
            artifact_dir=Path(os.getenv("ARTIFACT_DIR", str(base_dir / "artifacts"))),
            triage_policy_path=Path(os.getenv("TRIAGE_POLICY_PATH", str(base_dir / "triage_policy.json"))),
            platform_name=os.getenv("PLATFORM_NAME", "generic"),
            platform_base_url=os.getenv("PLATFORM_BASE_URL", ""),
            platform_login_url=os.getenv("PLATFORM_LOGIN_URL", "").strip(),
            platform_username=os.getenv("PLATFORM_USERNAME", "").strip(),
            platform_password=os.getenv("PLATFORM_PASSWORD", "").strip(),
            platform_tender_url_column=os.getenv("PLATFORM_TENDER_URL_COLUMN", "Ссылка"),
            platform_tender_id_column=os.getenv("PLATFORM_TENDER_ID_COLUMN", "Номер"),
            platform_tender_title_column=os.getenv("PLATFORM_TENDER_TITLE_COLUMN", "Наименование"),
            playwright_headless=_bool_env("PLAYWRIGHT_HEADLESS", True),
            tender_skip=_int_env("TENDER_SKIP", default=0) or 0,
            tender_limit=_int_env("TENDER_LIMIT"),
            local_files_dir=Path(os.getenv("LOCAL_FILES_DIR", str(base_dir / "manual_downloads"))),
            review_target_date=os.getenv("REVIEW_TARGET_DATE", "yesterday"),
            review_mode=os.getenv("REVIEW_MODE", "standard").strip().lower() or "standard",
            deep_review_max_files_per_tender=_int_env("DEEP_REVIEW_MAX_FILES_PER_TENDER", default=8) or 8,
            deep_review_max_chars_per_file=_int_env("DEEP_REVIEW_MAX_CHARS_PER_FILE", default=16000) or 16000,
            resolve_seldon_added_at=_bool_env("RESOLVE_SELDON_ADDED_AT", True),
            llm_preflight_checks=_int_env("LLM_PREFLIGHT_CHECKS", default=3) or 3,
            llm_preflight_required_successes=_int_env("LLM_PREFLIGHT_REQUIRED_SUCCESSES", default=2) or 2,
            tender_network_retries=_int_env("TENDER_NETWORK_RETRIES", default=3) or 3,
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
