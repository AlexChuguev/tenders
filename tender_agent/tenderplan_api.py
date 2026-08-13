from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class TenderplanApiConfig:
    base_url: str
    pat: str
    timeout_seconds: int = 30


class TenderplanApiError(RuntimeError):
    pass


class TenderplanApiClient:
    def __init__(self, config: TenderplanApiConfig) -> None:
        if not config.pat:
            raise TenderplanApiError("TENDERPLAN_PAT is not configured.")
        self.config = config

    def request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        query: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = self._build_url(path, query)
        body_bytes = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.config.pat}",
        }
        if json_body is not None:
            body_bytes = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(url=url, data=body_bytes, method=method.upper(), headers=headers)
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                payload = response.read().decode("utf-8")
                if not payload.strip():
                    return {}
                return json.loads(payload)
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="ignore")
            raise TenderplanApiError(f"HTTP {exc.code} {exc.reason}: {details}") from exc
        except URLError as exc:
            raise TenderplanApiError(f"Connection error: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise TenderplanApiError(f"Response is not valid JSON: {exc}") from exc

    def _build_url(self, path: str, query: dict[str, str] | None) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        url = f"{self.config.base_url.rstrip('/')}{normalized_path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        return url
