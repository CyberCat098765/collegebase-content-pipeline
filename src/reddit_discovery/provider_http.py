from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

import requests

from src.reddit_discovery.storage import atomic_write_json


DEFAULT_PUBLIC_USER_AGENT = (
    "CollegeBaseAdmissionsDiscovery/0.1 "
    "(contact: github.com/CyberCat098765/collegebase-content-pipeline)"
)
DEFAULT_CACHE_TTL_SECONDS = 24 * 60 * 60
logger = logging.getLogger(__name__)


class RequestLimitReached(RuntimeError):
    pass


class ProviderHttpError(RuntimeError):
    def __init__(self, message: str, *, attempts: int, retryable: bool) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.retryable = retryable


@dataclass(slots=True)
class HttpResponse:
    url: str
    status_code: int
    content_type: str
    body: str
    headers: dict[str, str] = field(default_factory=dict)
    from_cache: bool = False
    attempts: int = 1

    def json(self) -> Any:
        return json.loads(self.body)


@dataclass(slots=True)
class HttpMetrics:
    request_count: int = 0
    cache_hit_count: int = 0
    cache_miss_count: int = 0

    def snapshot(self) -> tuple[int, int, int]:
        return self.request_count, self.cache_hit_count, self.cache_miss_count


class BoundedHttpClient:
    def __init__(
        self,
        *,
        request_limit: int,
        cache_dir: str | Path | None = None,
        user_agent: str = DEFAULT_PUBLIC_USER_AGENT,
        timeout_seconds: float = 15.0,
        max_retries: int = 1,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        force_refresh: bool = False,
        session: Any | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if request_limit <= 0:
            raise ValueError("request_limit must be positive.")
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative.")
        self.request_limit = request_limit
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.user_agent = user_agent.strip() or DEFAULT_PUBLIC_USER_AGENT
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.cache_ttl_seconds = cache_ttl_seconds
        self.force_refresh = force_refresh
        self.session = session or requests.Session()
        self.sleep_fn = sleep_fn
        self.metrics = HttpMetrics()
        self._prune_expired_cache()

    def get(self, url: str, *, accept: str) -> HttpResponse:
        cached = None if self.force_refresh else self._read_cache(url)
        if cached is not None:
            self.metrics.cache_hit_count += 1
            return cached
        self.metrics.cache_miss_count += 1

        attempts = 0
        while True:
            self._reserve_request()
            attempts += 1
            try:
                response = self.session.get(
                    url,
                    headers={"User-Agent": self.user_agent, "Accept": accept},
                    timeout=self.timeout_seconds,
                    allow_redirects=True,
                )
            except requests.RequestException as exc:
                if attempts <= self.max_retries:
                    self.sleep_fn(min(2.0, 0.5 * attempts))
                    continue
                raise ProviderHttpError(
                    f"HTTP request failed: {type(exc).__name__}.",
                    attempts=attempts,
                    retryable=True,
                ) from exc

            normalized = _normalize_response(response, url, attempts)
            if normalized.status_code == 429 and attempts <= self.max_retries:
                delay = _retry_after_seconds(normalized.headers)
                if delay is None:
                    delay = min(2.0, 0.5 * attempts)
                if delay <= 30:
                    self.sleep_fn(delay)
                    continue
            elif 500 <= normalized.status_code < 600 and attempts <= self.max_retries:
                self.sleep_fn(min(2.0, 0.5 * attempts))
                continue

            if normalized.status_code == 200:
                self._write_cache(normalized)
            return normalized

    def _reserve_request(self) -> None:
        if self.metrics.request_count >= self.request_limit:
            raise RequestLimitReached(
                f"HTTP request cap reached ({self.request_limit})."
            )
        self.metrics.request_count += 1

    def _cache_path(self, url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, url: str) -> HttpResponse | None:
        path = self._cache_path(url)
        if path is None or not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            fetched_at = float(payload["fetched_at_epoch"])
            if time.time() - fetched_at > self.cache_ttl_seconds:
                return None
            if payload.get("url") != url or int(payload.get("status_code", 0)) != 200:
                return None
            return HttpResponse(
                url=url,
                status_code=200,
                content_type=str(payload.get("content_type", "")),
                body=str(payload.get("body", "")),
                headers=_string_mapping(payload.get("headers")),
                from_cache=True,
                attempts=0,
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            logger.warning("Ignoring unreadable Reddit HTTP cache entry: %s", path.name)
            return None

    def _write_cache(self, response: HttpResponse) -> None:
        path = self._cache_path(response.url)
        if path is None:
            return
        atomic_write_json(
            path,
            {
                "url": response.url,
                "status_code": response.status_code,
                "content_type": response.content_type,
                "headers": {
                    name: value
                    for name, value in response.headers.items()
                    if name in {"etag", "last-modified"}
                },
                "body": response.body,
                "fetched_at_epoch": time.time(),
            },
        )

    def _prune_expired_cache(self) -> None:
        if self.cache_dir is None or not self.cache_dir.is_dir():
            return
        now = time.time()
        for path in self.cache_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                fetched_at = float(payload["fetched_at_epoch"])
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                fetched_at = 0.0
            if now - fetched_at <= self.cache_ttl_seconds:
                continue
            try:
                path.unlink()
            except OSError:
                logger.warning("Could not remove expired Reddit HTTP cache entry: %s", path.name)


def metrics_delta(
    before: tuple[int, int, int],
    after: tuple[int, int, int],
) -> tuple[int, int, int]:
    return tuple(end - start for start, end in zip(before, after, strict=True))


def _normalize_response(response: Any, requested_url: str, attempts: int) -> HttpResponse:
    headers = {
        str(name).casefold(): str(value)
        for name, value in dict(getattr(response, "headers", {})).items()
    }
    body = getattr(response, "text", "")
    if not isinstance(body, str):
        body = str(body)
    return HttpResponse(
        url=str(getattr(response, "url", requested_url) or requested_url),
        status_code=int(getattr(response, "status_code", 0)),
        content_type=headers.get("content-type", ""),
        body=body,
        headers=headers,
        attempts=attempts,
    )


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    value = headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _string_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(name): str(item) for name, item in value.items()}
