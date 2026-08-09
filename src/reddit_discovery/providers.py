from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.reddit_discovery.atom_provider import (
    acquire_curated_a2c,
    acquire_manual_urls,
    acquire_rss,
)
from src.reddit_discovery.auth import MissingRedditCredentials, credentials_from_environment
from src.reddit_discovery.discovery import merge_candidates_by_id_and_url
from src.reddit_discovery.models import DiscoveryResult
from src.reddit_discovery.options import RedditDiscoveryOptions
from src.reddit_discovery.provider_http import BoundedHttpClient, metrics_delta
from src.reddit_discovery.provider_models import ProviderAcquisition, ProviderStatus
from src.reddit_discovery.public_json_provider import acquire_public_json


DEFAULT_QUICK_HTTP_REQUEST_LIMIT = 20
DEFAULT_MAX_HTTP_REQUEST_LIMIT = 75
DEFAULT_QUICK_CURATED_DETAILS = 12
DEFAULT_MAX_CURATED_DETAILS = 40


def resolve_provider(options: RedditDiscoveryOptions) -> str:
    if options.input_path is not None:
        return "import"
    if options.provider != "auto":
        return options.provider
    return "praw" if praw_credentials_available() else "free-auto"


def praw_credentials_available() -> bool:
    try:
        credentials_from_environment(load_env=True)
    except MissingRedditCredentials:
        return False
    return True


def free_provider_plan(provider: str) -> tuple[str, ...]:
    if provider in {"auto", "free-auto"}:
        return ("curated", "rss")
    if provider in {"curated", "rss", "public-json", "manual"}:
        return (provider,)
    return ()


def acquire_free_candidates(
    options: RedditDiscoveryOptions,
    *,
    retrieved_at: str,
    client: BoundedHttpClient | None = None,
) -> ProviderAcquisition:
    candidate_limit = options.candidate_limit or 500
    request_limit = (
        DEFAULT_QUICK_HTTP_REQUEST_LIMIT
        if options.quick
        else DEFAULT_MAX_HTTP_REQUEST_LIMIT
    )
    http = client or BoundedHttpClient(
        request_limit=request_limit,
        cache_dir=Path(options.output_dir) / ".reddit_http_cache",
        force_refresh=options.force,
    )
    batches: list[DiscoveryResult] = []
    statuses: list[ProviderStatus] = []

    for provider in free_provider_plan(resolve_provider(options)):
        remaining = candidate_limit - sum(len(batch.candidates) for batch in batches)
        if remaining <= 0:
            break
        before = http.metrics.snapshot()
        batch = _run_provider(
            provider,
            http,
            options,
            remaining,
            retrieved_at,
        )
        after = http.metrics.snapshot()
        request_count, cache_hits, cache_misses = metrics_delta(before, after)
        statuses.append(
            _provider_status(
                provider,
                batch,
                request_count=request_count,
                cache_hits=cache_hits,
                cache_misses=cache_misses,
            )
        )
        batches.append(batch)

    discovery = merge_discovery_results(batches, candidate_limit=candidate_limit)
    return ProviderAcquisition(
        discovery=discovery,
        statuses=statuses,
        request_count=http.metrics.request_count,
        cache_hit_count=http.metrics.cache_hit_count,
        cache_miss_count=http.metrics.cache_miss_count,
    )


def merge_discovery_results(
    results: list[DiscoveryResult],
    *,
    candidate_limit: int,
) -> DiscoveryResult:
    candidates = [
        candidate
        for result in results
        for candidate in [*result.candidates, *result.duplicates]
    ]
    unique, duplicates = merge_candidates_by_id_and_url(candidates)
    limited = unique[:candidate_limit]
    return DiscoveryResult(
        candidates=limited,
        duplicates=duplicates,
        errors=[error for result in results for error in result.errors],
        route_counts={
            route: count
            for result in results
            for route, count in result.route_counts.items()
        },
        completed_routes=[
            route for result in results for route in result.completed_routes
        ],
        limit_reached=len(unique) >= candidate_limit,
    )


def _run_provider(
    provider: str,
    client: BoundedHttpClient,
    options: RedditDiscoveryOptions,
    remaining: int,
    retrieved_at: str,
) -> DiscoveryResult:
    runners: dict[str, Callable[[], DiscoveryResult]] = {
        "curated": lambda: acquire_curated_a2c(
            client,
            candidate_limit=remaining,
            detail_limit=min(
                remaining,
                DEFAULT_QUICK_CURATED_DETAILS
                if options.quick
                else DEFAULT_MAX_CURATED_DETAILS,
            ),
            retrieved_at=retrieved_at,
        ),
        "rss": lambda: acquire_rss(
            client,
            subreddit=options.subreddit,
            candidate_limit=remaining,
            retrieved_at=retrieved_at,
        ),
        "public-json": lambda: acquire_public_json(
            client,
            subreddit=options.subreddit,
            candidate_limit=remaining,
            max_pages_per_route=1 if options.quick else 2,
            retrieved_at=retrieved_at,
        ),
        "manual": lambda: acquire_manual_urls(
            client,
            urls=options.reddit_urls,
            candidate_limit=remaining,
            retrieved_at=retrieved_at,
        ),
    }
    return runners[provider]()


def _provider_status(
    provider: str,
    result: DiscoveryResult,
    *,
    request_count: int,
    cache_hits: int,
    cache_misses: int,
) -> ProviderStatus:
    first_error = result.errors[0] if result.errors else None
    if result.candidates and result.errors:
        status = "completed_with_errors"
    elif result.candidates:
        status = "completed"
    elif result.errors:
        status = "unavailable"
    else:
        status = "completed_empty"
    return ProviderStatus(
        provider=provider,
        status=status,
        candidate_count=len(result.candidates),
        request_count=request_count,
        cache_hit_count=cache_hits,
        cache_miss_count=cache_misses,
        status_code=first_error.status_code if first_error else None,
        error=first_error.message if first_error else "",
        intended_use=(
            "diagnostic/development only"
            if provider == "public-json"
            else "development/calibration"
        ),
    )


def stop_reason(
    *,
    discovery_limit_reached: bool,
    provider_statuses: list[dict[str, Any]],
) -> str:
    if discovery_limit_reached:
        return "candidate_limit_reached"
    status_codes = {status.get("status_code") for status in provider_statuses}
    if 429 in status_codes:
        return "rate_limited"
    if 403 in status_codes:
        return "access_denied"
    unavailable = {"unavailable", "skipped_missing_credentials"}
    if provider_statuses and all(
        status.get("status") in unavailable for status in provider_statuses
    ):
        return "providers_unavailable"
    if any(status.get("status") == "completed_with_errors" for status in provider_statuses):
        return "completed_with_errors"
    return "completed"
