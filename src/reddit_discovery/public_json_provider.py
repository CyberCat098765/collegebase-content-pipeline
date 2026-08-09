from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import urlencode

from src.reddit_discovery.discovery import merge_candidates_by_id_and_url
from src.reddit_discovery.models import DiscoveryError, DiscoveryResult, RedditCandidate
from src.reddit_discovery.provider_http import (
    BoundedHttpClient,
    ProviderHttpError,
    RequestLimitReached,
)
from src.reddit_discovery.provider_normalization import candidate_from_public_json
from src.time_utils import utc_now


@dataclass(frozen=True, slots=True)
class PublicJsonRoute:
    name: str
    path: str
    params: tuple[tuple[str, str], ...]


PUBLIC_JSON_ROUTES = (
    PublicJsonRoute("public_json_top", "top.json", (("t", "year"),)),
    PublicJsonRoute("public_json_new", "new.json", ()),
    PublicJsonRoute(
        "public_json_search",
        "search.json",
        (
            ("q", 'flair_name:"Best of A2C"'),
            ("restrict_sr", "1"),
            ("sort", "top"),
            ("t", "all"),
        ),
    ),
)


def acquire_public_json(
    client: BoundedHttpClient,
    *,
    subreddit: str,
    candidate_limit: int,
    max_pages_per_route: int = 2,
    routes: Iterable[PublicJsonRoute] = PUBLIC_JSON_ROUTES,
    retrieved_at: str | None = None,
) -> DiscoveryResult:
    if candidate_limit <= 0:
        raise ValueError("candidate_limit must be positive.")
    if max_pages_per_route <= 0:
        raise ValueError("max_pages_per_route must be positive.")
    acquired: list[RedditCandidate] = []
    errors: list[DiscoveryError] = []
    route_counts: dict[str, int] = {}
    completed: list[str] = []
    stop_provider = False
    retrieved = retrieved_at or utc_now()

    for route in routes:
        after = ""
        route_count = 0
        route_completed = False
        for page in range(1, max_pages_per_route + 1):
            url = public_json_route_url(
                subreddit,
                route,
                limit=min(100, candidate_limit - len(acquired)),
                after=after,
            )
            try:
                response = client.get(url, accept="application/json")
            except (ProviderHttpError, RequestLimitReached) as exc:
                errors.append(_request_error(route.name, exc))
                stop_provider = isinstance(exc, RequestLimitReached)
                break

            if response.status_code != 200:
                errors.append(_status_error(route.name, response.status_code, response.attempts))
                stop_provider = response.status_code in {403, 429}
                break
            if "json" not in response.content_type.casefold():
                errors.append(
                    DiscoveryError(
                        route=route.name,
                        error_type="UnexpectedContentType",
                        message="Reddit public JSON returned a non-JSON response.",
                        retryable=False,
                        attempts=response.attempts,
                        status_code=response.status_code,
                    )
                )
                break
            try:
                payload = response.json()
            except (json.JSONDecodeError, ValueError, TypeError):
                errors.append(
                    DiscoveryError(
                        route=route.name,
                        error_type="MalformedJson",
                        message="Reddit public JSON response could not be decoded.",
                        retryable=False,
                        attempts=response.attempts,
                        status_code=response.status_code,
                    )
                )
                break

            page_records, next_after, schema_error = _listing_records(payload)
            if schema_error:
                errors.append(
                    DiscoveryError(
                        route=route.name,
                        error_type="UnexpectedSchema",
                        message=schema_error,
                        retryable=False,
                        attempts=response.attempts,
                        status_code=response.status_code,
                    )
                )
                break
            for index, record in enumerate(page_records, start=1):
                try:
                    candidate = candidate_from_public_json(
                        record,
                        discovered_by=route.name,
                        retrieved_at=retrieved,
                        response_url=response.url,
                    )
                except ValueError as exc:
                    errors.append(
                        DiscoveryError(
                            route=f"{route.name}:page-{page}:item-{index}",
                            error_type="CandidateNormalizationError",
                            message=str(exc),
                            retryable=False,
                            attempts=1,
                        )
                    )
                    continue
                acquired.append(candidate)
                route_count += 1
                if len(acquired) >= candidate_limit:
                    break
            if len(acquired) >= candidate_limit:
                break
            if not next_after:
                route_completed = True
                break
            after = next_after
        route_counts[route.name] = route_count
        if route_completed or len(acquired) >= candidate_limit:
            completed.append(route.name)
        if stop_provider or len(acquired) >= candidate_limit:
            break

    unique, duplicates = merge_candidates_by_id_and_url(acquired)
    return DiscoveryResult(
        candidates=unique,
        duplicates=duplicates,
        errors=errors,
        route_counts=route_counts,
        completed_routes=completed,
        limit_reached=len(unique) >= candidate_limit,
    )


def public_json_route_url(
    subreddit: str,
    route: PublicJsonRoute,
    *,
    limit: int,
    after: str,
) -> str:
    params = [*route.params, ("limit", str(max(1, limit))), ("raw_json", "1")]
    if after:
        params.append(("after", after))
    return f"https://www.reddit.com/r/{subreddit}/{route.path}?{urlencode(params)}"


def _listing_records(
    payload: Any,
) -> tuple[list[Mapping[str, Any]], str, str]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), Mapping):
        return [], "", "Reddit public JSON is missing the data object."
    data = payload["data"]
    children = data.get("children")
    if not isinstance(children, list):
        return [], "", "Reddit public JSON is missing the data.children list."
    records = [
        child["data"]
        for child in children
        if isinstance(child, Mapping) and isinstance(child.get("data"), Mapping)
    ]
    after = data.get("after")
    return records, str(after).strip() if after is not None else "", ""


def _status_error(route: str, status_code: int, attempts: int) -> DiscoveryError:
    messages = {
        403: "Reddit public JSON access was denied; this provider stopped without bypass attempts.",
        429: "Reddit public JSON remained rate limited after bounded retry.",
    }
    return DiscoveryError(
        route=route,
        error_type=f"HTTP{status_code}",
        message=messages.get(status_code, f"Reddit public JSON returned HTTP {status_code}."),
        retryable=status_code == 429 or 500 <= status_code < 600,
        attempts=attempts,
        status_code=status_code,
    )


def _request_error(route: str, error: Exception) -> DiscoveryError:
    return DiscoveryError(
        route=route,
        error_type=type(error).__name__,
        message=str(error),
        retryable=bool(getattr(error, "retryable", False)),
        attempts=int(getattr(error, "attempts", 0)),
    )
