"""Bounded JSON HTTP requests. POSTs are never automatically retried."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class ServiceError(RuntimeError):
    pass


class JSONClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 20,
                 user_agent: str = "resolve-hackathon-starter/0.1"):
        parsed = urlparse(base_url)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("API base URL must use HTTPS (local development may use HTTP)")
        self.base_url, self.token, self.timeout = base_url.rstrip("/"), token, timeout
        self.user_agent = user_agent

    def request(self, method: str, path: str, body: dict | None = None):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("API path must be relative to the configured service")
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(self.base_url + path, data=data, method=method, headers={
            "Authorization": self.token,
            "Accept": "application/json", "Content-Type": "application/json",
            "User-Agent": self.user_agent,
        })
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ServiceError("API response exceeded size limit")
                return json.loads(raw)
        except HTTPError as error:
            # No bodies, headers, or tokens in logs/state.
            raise ServiceError(f"Service returned HTTP {error.code}") from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise ServiceError("Service request failed or returned invalid JSON") from None
