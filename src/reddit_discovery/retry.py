from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any, TypeVar

from src.reddit_discovery.auth import safe_auth_error_message
from src.reddit_discovery.models import DiscoveryError


T = TypeVar("T")
SleepFunction = Callable[[float], None]
logger = logging.getLogger(__name__)

_PERMANENT_ERROR_TYPES = {"OAuthException", "Forbidden", "NotFound"}
_RETRYABLE_ERROR_TYPES = {
    "TooManyRequests",
    "RequestException",
    "ResponseException",
    "Timeout",
    "ConnectTimeout",
    "ReadTimeout",
    "ConnectionError",
    "ServerError",
    "JSONDecodeError",
}


def run_with_retries(
    operation: Callable[[], T],
    *,
    route: str,
    max_retries: int,
    backoff_base_seconds: float,
    sleep_fn: SleepFunction,
) -> tuple[T | None, DiscoveryError | None]:
    max_attempts = max(1, max_retries + 1)
    for attempt in range(1, max_attempts + 1):
        try:
            return operation(), None
        except Exception as exc:
            retryable = is_retryable_reddit_error(exc)
            if not retryable or attempt >= max_attempts:
                return None, discovery_error(exc, route, retryable, attempt)
            delay = retry_delay(exc, attempt, backoff_base_seconds)
            _log_retry(route, exc, attempt, max_attempts, delay)
            if delay > 0:
                sleep_fn(delay)
    raise AssertionError("Retry loop exited unexpectedly.")


def collect_iterable_with_retries(
    operation: Callable[[], Iterable[T]],
    *,
    route: str,
    max_retries: int,
    backoff_base_seconds: float,
    sleep_fn: SleepFunction,
    item_key: Callable[[T], Any],
) -> tuple[list[T], DiscoveryError | None]:
    """Retry a lazy listing while retaining unique items yielded before failure."""

    collected: list[T] = []
    seen: set[Any] = set()
    max_attempts = max(1, max_retries + 1)
    for attempt in range(1, max_attempts + 1):
        try:
            for item in operation():
                key = item_key(item)
                if key in seen:
                    continue
                seen.add(key)
                collected.append(item)
            return collected, None
        except Exception as exc:
            retryable = is_retryable_reddit_error(exc)
            if not retryable or attempt >= max_attempts:
                return collected, discovery_error(exc, route, retryable, attempt)
            delay = retry_delay(exc, attempt, backoff_base_seconds)
            _log_retry(route, exc, attempt, max_attempts, delay)
            if delay > 0:
                sleep_fn(delay)
    raise AssertionError("Retry loop exited unexpectedly.")


def is_retryable_reddit_error(error: BaseException) -> bool:
    type_names = {cls.__name__ for cls in type(error).__mro__}
    if type_names & _PERMANENT_ERROR_TYPES:
        return False
    if "BadJSON" in type_names:
        return True
    if "ResponseException" in type_names:
        status = _response_status(error)
        if status in {400, 401, 403, 404}:
            return False
        return status is None or status == 429 or status >= 500
    return bool(type_names & _RETRYABLE_ERROR_TYPES)


def retry_delay(error: BaseException, attempt: int, base_seconds: float) -> float:
    exponential = max(0.0, base_seconds) * (2 ** (attempt - 1))
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {})
    retry_after = 0.0
    if hasattr(headers, "get"):
        value = headers.get("retry-after")
        if value in {None, ""}:
            value = headers.get("Retry-After")
        retry_after = _optional_float(value) or 0.0
    return max(min(30.0, exponential), retry_after)


def should_abort_after_reddit_error(
    error: DiscoveryError,
    *,
    not_found_is_global: bool = True,
    forbidden_is_global: bool = True,
) -> bool:
    """Stop request fan-out after an exhausted global auth or rate-limit failure."""

    if error.error_type == "NotFound":
        return not_found_is_global
    if error.error_type == "Forbidden":
        return forbidden_is_global
    if error.error_type in {"OAuthException", "TooManyRequests"}:
        return True
    if error.status_code == 403:
        return forbidden_is_global
    return error.status_code in {401, 429}


def discovery_error(
    error: BaseException, route: str, retryable: bool, attempts: int
) -> DiscoveryError:
    error_type = type(error).__name__
    status = _response_status(error)
    if error_type in _PERMANENT_ERROR_TYPES or (
        error_type == "ResponseException" and status in {401, 403}
    ):
        message = safe_auth_error_message(error)
    elif error_type == "ResponseException" and status is not None:
        message = f"Reddit API route failed: ResponseException (HTTP {status})."
    else:
        message = f"Reddit API route failed: {error_type}."
    return DiscoveryError(route, error_type, message, retryable, attempts, status)


def _log_retry(
    route: str,
    error: BaseException,
    attempt: int,
    max_attempts: int,
    delay: float,
) -> None:
    logger.warning(
        "Retrying Reddit route %s after %s (attempt %d/%d, delay %.1fs).",
        route,
        type(error).__name__,
        attempt,
        max_attempts,
        delay,
    )


def _response_status(error: BaseException) -> int | None:
    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: object) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
