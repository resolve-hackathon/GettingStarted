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
                 user_agent: str = "resolve-agent/0.1", headers: dict | None = None):
        parsed = urlparse(base_url)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("API base URL must use HTTPS (local development may use HTTP)")
        self.base_url, self.token, self.timeout = base_url.rstrip("/"), token, timeout
        self.user_agent = user_agent
        self.extra_headers = dict(headers or {})

    def request(self, method: str, path: str, body: dict | None = None):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("API path must be relative to the configured service")
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(self.base_url + path, data=data, method=method, headers={
            "Authorization": self.token,
            "Accept": "application/json", "Content-Type": "application/json",
            "User-Agent": self.user_agent, **self.extra_headers,
        })
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
        except HTTPError as error:
            # No bodies, headers, or tokens in logs/state.
            raise ServiceError(f"Service returned HTTP {error.code}") from None
        except (TimeoutError, OSError) as error:
            if "timed out" in str(error).lower() or isinstance(error, TimeoutError):
                raise ServiceError(f"Service did not answer within {self.timeout:.0f}s") from None
            raise ServiceError("Service unreachable or connection failed") from None
        if len(raw) > 2_000_000:
            raise ServiceError("API response exceeded size limit")
        try:
            return json.loads(raw)
        except ValueError:
            streamed = _parse_sse(raw)
            if streamed is not None:
                return streamed
            raise ServiceError("Service returned a body that is not JSON") from None


def _parse_sse(raw: bytes):
    """Join an OpenAI-style event stream into one chat.completion body, or None."""
    text = raw.decode("utf-8", "replace")
    if "data:" not in text:
        return None
    parts, model, finish, last = [], None, None, None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(chunk, dict):
            continue
        last, model = chunk, chunk.get("model", model)
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or choice.get("message") or {}
            if isinstance(delta.get("content"), str):
                parts.append(delta["content"])
            finish = choice.get("finish_reason") or finish
    if last is None:
        return None
    body = {"model": model, "choices": [{"finish_reason": finish,
                                         "message": {"role": "assistant", "content": "".join(parts)}}]}
    if isinstance(last.get("meterless"), dict):
        body["meterless"] = last["meterless"]
    return body
