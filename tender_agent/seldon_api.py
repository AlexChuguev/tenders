from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class SeldonApiConfig:
    base_url: str
    login: str
    password: str
    timeout_seconds: int = 30


class SeldonApiError(RuntimeError):
    pass


class SeldonApiClient:
    def __init__(self, config: SeldonApiConfig) -> None:
        if not config.login or not config.password:
            raise SeldonApiError("SELDON_API_LOGIN and SELDON_API_PASSWORD must be configured.")
        self.config = config
        self._token: str | None = None

    def login(self, force: bool = False) -> str:
        if self._token and not force:
            return self._token
        payload = self.request_json(
            "/User/Login",
            method="POST",
            json_body={
                "name": self.config.login,
                "password": self.config.password,
            },
            include_token=False,
        )
        token = str(((payload or {}).get("result") or {}).get("token") or "").strip()
        if not token:
            raise SeldonApiError(f"Seldon login did not return token: {json.dumps(payload, ensure_ascii=False)}")
        self._token = token
        return token

    def request_json(
        self,
        path: str,
        *,
        method: str = "POST",
        query: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        include_token: bool = True,
    ) -> Any:
        request_query = dict(query or {})
        if include_token:
            request_query["token"] = self.login()
        url = self._build_url(path, request_query)
        body_bytes = None
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if json_body is not None:
            body_bytes = json.dumps(json_body).encode("utf-8")

        request = Request(url=url, data=body_bytes, method=method.upper(), headers=headers)
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                payload = response.read().decode("utf-8")
                if not payload.strip():
                    return {}
                return json.loads(payload)
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="ignore")
            raise SeldonApiError(f"HTTP {exc.code} {exc.reason}: {details}") from exc
        except URLError as exc:
            raise SeldonApiError(f"Connection error: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise SeldonApiError(f"Response is not valid JSON: {exc}") from exc

    def _build_url(self, path: str, query: dict[str, str] | None) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        url = f"{self.config.base_url.rstrip('/')}{normalized_path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        return url
