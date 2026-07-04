from __future__ import annotations

import json
import traceback
import time
import subprocess
from datetime import datetime
from pathlib import Path

from openai import OpenAI
from pypdf import PdfReader


class OpenAICompatibleProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        max_chars_per_file: int = 16000,
        total_context_chars: int | None = None,
    ) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url or None) if api_key else None
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.max_chars_per_file = max(6000, max_chars_per_file)
        self.total_context_chars = total_context_chars or max(64000, self.max_chars_per_file * 8)
        self.last_budget_report: dict[str, object] = {}

    def analyze_documents(self, prompt: str, files: list[Path]) -> str:
        if self.client is None:
            raise RuntimeError("LLM API key is not configured.")

        text_prompt = self._build_text_fallback(prompt, files)
        try:
            return self._analyze_via_chat_completions(text_prompt)
        except Exception as exc:
            _log_llm_exception("chat_completions", exc, files, extra={"content_mode": "text_only"})
            try:
                return self._analyze_via_curl(text_prompt)
            except Exception as curl_exc:
                _log_llm_exception("chat_completions_curl_fallback", curl_exc, files, extra={"content_mode": "text_only"})
                raise _normalize_exception(curl_exc) from exc

    def health_check(self, checks: int = 3, required_successes: int = 2) -> tuple[bool, str]:
        if self.client is None:
            return False, "LLM API key is not configured."
        successes = 0
        last_error = ""
        for _ in range(max(1, checks)):
            try:
                reply = self._analyze_via_chat_completions("Ответь одним словом: ok")
                if reply.strip():
                    successes += 1
                if successes >= required_successes:
                    return True, ""
            except Exception as exc:
                last_error = str(_normalize_exception(exc))
                try:
                    reply = self._analyze_via_curl("Ответь одним словом: ok")
                    if reply.strip():
                        successes += 1
                    if successes >= required_successes:
                        return True, ""
                except Exception as curl_exc:
                    last_error = str(_normalize_exception(curl_exc))
            time.sleep(1)
        return False, last_error or "health check failed"

    def _with_retries(self, operation):
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                return operation()
            except Exception as exc:
                last_exc = exc
                message = str(exc)
                if not _is_retryable_error(message):
                    raise
                if attempt == 2:
                    break
                time.sleep(2 * (attempt + 1))
        assert last_exc is not None
        raise _normalize_exception(last_exc)

    def _build_text_fallback(self, prompt: str, files: list[Path]) -> str:
        parts = [prompt, "\n\nДополнительный контекст из файлов:\n"]
        budget = self.total_context_chars
        included: list[dict[str, object]] = []
        dropped: list[dict[str, object]] = []
        for file_path in files:
            chunk = _extract_text_snippet(file_path, max_chars=self.max_chars_per_file)
            if not chunk:
                continue
            block = f"\n[file: {file_path.name}]\n{chunk}\n"
            if budget - len(block) <= 0:
                dropped.append(
                    {
                        "name": file_path.name,
                        "reason": "total_context_budget_exceeded",
                        "block_chars": len(block),
                    }
                )
                break
            parts.append(block)
            budget -= len(block)
            included.append(
                {
                    "name": file_path.name,
                    "block_chars": len(block),
                }
            )
        self.last_budget_report = {
            "max_chars_per_file": self.max_chars_per_file,
            "total_context_chars": self.total_context_chars,
            "remaining_chars": budget,
            "included_files": included,
            "dropped_files": dropped,
        }
        return "".join(parts)

    def _analyze_via_chat_completions(self, prompt: str) -> str:
        response = self._with_retries(
            lambda: self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
            )
        )
        try:
            return response.choices[0].message.content.strip()
        except Exception as exc:
            raise RuntimeError(f"Unexpected OpenAI chat.completions response: {response}") from exc

    def _analyze_via_curl(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                config = "\n".join(
                    [
                        "fail",
                        "show-error",
                        "silent",
                        f'url = "{_curl_config_quote(f"{self.base_url}/chat/completions")}"',
                        f'header = "{_curl_config_quote(f"Authorization: Bearer {self.api_key}")}"',
                        'header = "Content-Type: application/json"',
                        f'data-binary = "{_curl_config_quote(json.dumps(payload, ensure_ascii=False))}"',
                    ]
                )
                result = subprocess.run(
                    [
                        "curl",
                        "-K",
                        "-",
                    ],
                    check=True,
                    capture_output=True,
                    input=config.encode("utf-8"),
                )
                response = json.loads(result.stdout.decode("utf-8"))
                try:
                    return response["choices"][0]["message"]["content"].strip()
                except Exception as exc:
                    raise RuntimeError(f"Unexpected curl OpenAI response: {response}") from exc
            except Exception as exc:
                last_exc = exc
                if not _is_retryable_curl_error(exc) or attempt == 2:
                    break
                time.sleep(2 * (attempt + 1))
        assert last_exc is not None
        raise last_exc


def _is_retryable_error(message: str) -> bool:
    lowered = message.lower()
    retryable_markers = [
        "connection error",
        "timed out",
        "timeout",
        "temporarily unavailable",
        "server error",
        "502",
        "503",
        "504",
    ]
    non_retryable_markers = [
        "rate_limit_exceeded",
        "request too large",
        "invalid input",
        "invalid_request_error",
        "unsupported format",
        "authentication",
        "permission",
        "401",
        "403",
        "404",
        "429",
    ]
    if any(marker in lowered for marker in non_retryable_markers):
        return False
    return any(marker in lowered for marker in retryable_markers)


def _extract_text_snippet(path: Path, max_chars: int) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix in {".txt", ".md", ".markdown", ".csv", ".json", ".xml", ".html", ".htm", ".rtf"}:
            return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
        if suffix == ".pdf":
            parts: list[str] = []
            for page in PdfReader(str(path)).pages[:8]:
                text = page.extract_text() or ""
                if text.strip():
                    parts.append(text)
                if sum(len(p) for p in parts) >= max_chars:
                    break
            return "\n".join(parts)[:max_chars]
        if suffix in {".doc", ".docx", ".xls", ".xlsx"}:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except Exception:
        return ""


def _curl_config_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")


def _log_llm_exception(stage: str, exc: Exception, files: list[Path], extra: dict | None = None) -> None:
    log_path = Path.cwd() / "llm_errors.log"
    lines = [
        f"[{datetime.now().isoformat(timespec='seconds')}] stage={stage}",
        f"exception_type={exc.__class__.__name__}",
        f"exception_repr={exc!r}",
        f"exception_str={str(exc)}",
        f"files={[path.name for path in files]}",
    ]
    if extra:
        for key, value in extra.items():
            lines.append(f"{key}={value}")
    lines.append("traceback:")
    lines.append(traceback.format_exc().rstrip())
    lines.append("-" * 80)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _normalize_exception(exc: Exception) -> Exception:
    chain = _exception_chain_text(exc).lower()
    if "nodename nor servname provided" in chain or "name or service not known" in chain:
        return RuntimeError("DNS resolution failed while connecting to OpenAI API.")
    return exc


def _is_dns_resolution_error(exc: Exception) -> bool:
    return "dns resolution failed" in str(exc).lower()


def _is_retryable_curl_error(exc: Exception) -> bool:
    if isinstance(exc, subprocess.CalledProcessError):
        if exc.returncode in {5, 6, 7, 28, 52, 56}:
            return True
        stderr = (exc.stderr or b"").decode("utf-8", errors="ignore").lower()
        return any(marker in stderr for marker in ["timed out", "timeout", "connection reset", "could not resolve host"])
    message = str(exc).lower()
    return any(marker in message for marker in ["dns resolution failed", "timed out", "timeout", "connection reset"])


def _exception_chain_text(exc: Exception) -> str:
    parts = [repr(exc), str(exc)]
    current = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        current = current.__cause__ or current.__context__
        if current is not None:
            parts.append(repr(current))
            parts.append(str(current))
    return "\n".join(parts)
