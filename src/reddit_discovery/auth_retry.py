from __future__ import annotations

import time
from typing import Any

from src.reddit_discovery.auth import (
    INVALID_CREDENTIALS_NEXT_STEP,
    probe_reddit_auth_unchecked,
    reddit_rate_limits,
)
from src.reddit_discovery.constants import DEFAULT_MAX_RETRIES, SUPPORTED_SUBREDDIT
from src.reddit_discovery.models import AuthProbeResult
from src.reddit_discovery.retry import SleepFunction, run_with_retries


TRANSIENT_AUTH_NEXT_STEP = (
    "Retry the authentication check later; Reddit may be temporarily unavailable "
    "or rate-limiting requests."
)


def probe_reddit_auth_with_retries(
    reddit: Any,
    subreddit_name: str = SUPPORTED_SUBREDDIT,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_base_seconds: float = 1.0,
    sleep_fn: SleepFunction = time.sleep,
) -> AuthProbeResult:
    result, error = run_with_retries(
        lambda: probe_reddit_auth_unchecked(reddit, subreddit_name),
        route="authentication",
        max_retries=max_retries,
        backoff_base_seconds=backoff_base_seconds,
        sleep_fn=sleep_fn,
    )
    if result is not None:
        return result
    assert error is not None
    return AuthProbeResult(
        success=False,
        read_only=bool(getattr(reddit, "read_only", False)),
        subreddit=subreddit_name,
        subreddit_title="",
        accessible=False,
        rate_limit=reddit_rate_limits(reddit),
        error=error.message,
        next_step=(
            TRANSIENT_AUTH_NEXT_STEP if error.retryable else INVALID_CREDENTIALS_NEXT_STEP
        ),
    )
