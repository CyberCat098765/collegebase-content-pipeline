from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping

from src.reddit_discovery.atom_provider import (
    CURATED_A2C_SOURCES,
    extract_curated_post_urls,
    parse_atom_candidates,
    parse_atom_root,
    post_feed_url,
    rss_routes,
)
from src.reddit_discovery.auth import (
    MissingRedditCredentials,
    RedditAuthError,
    create_reddit_client,
    credentials_from_environment,
)
from src.reddit_discovery.auth_retry import probe_reddit_auth_with_retries
from src.reddit_discovery.provider_http import (
    BoundedHttpClient,
    ProviderHttpError,
    RequestLimitReached,
    metrics_delta,
)
from src.reddit_discovery.provider_normalization import ATOM
from src.reddit_discovery.public_json_provider import (
    PUBLIC_JSON_ROUTES,
    PublicJsonRoute,
    public_json_route_url,
)
from src.time_utils import utc_now


@dataclass(slots=True)
class CapabilityResult:
    provider: str
    status: str
    credentials: str
    cost: str
    live_tested: bool
    useful_data: str
    limitations: str
    request_count: int = 0
    status_code: int | None = None
    content_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_reddit_capabilities(
    *,
    client: BoundedHttpClient | None = None,
    environ: Mapping[str, str] | None = None,
    praw_checker: Callable[[], CapabilityResult] | None = None,
) -> list[CapabilityResult]:
    http = client or BoundedHttpClient(request_limit=7, cache_dir=None, max_retries=0)
    results = [
        _probe_public_json(http, route)
        for route in PUBLIC_JSON_ROUTES
    ]
    results.append(_probe_curated(http))
    results.append(_probe_rss(http))
    results.append(
        praw_checker()
        if praw_checker is not None
        else _probe_praw(environ=environ)
    )
    results.append(
        CapabilityResult(
            provider="import_json",
            status="PASS",
            credentials="none",
            cost="$0",
            live_tested=False,
            useful_data="Supplied normalized JSON or JSONL records",
            limitations="Offline input only",
        )
    )
    return results


def _probe_public_json(
    client: BoundedHttpClient,
    route: PublicJsonRoute,
) -> CapabilityResult:
    before = client.metrics.snapshot()
    url = public_json_route_url(
        "ApplyingToCollege",
        route,
        limit=5,
        after="",
    )
    try:
        response = client.get(url, accept="application/json")
    except (ProviderHttpError, RequestLimitReached) as exc:
        requests, _, _ = metrics_delta(before, client.metrics.snapshot())
        return _failed_capability(route.name, exc, request_count=requests)
    requests, _, _ = metrics_delta(before, client.metrics.snapshot())
    count = 0
    valid = False
    if response.status_code == 200 and "json" in response.content_type.casefold():
        try:
            payload = json.loads(response.body)
            children = payload.get("data", {}).get("children", [])
            valid = isinstance(children, list)
            count = len(children) if valid else 0
        except (json.JSONDecodeError, AttributeError, TypeError):
            valid = False
    return CapabilityResult(
        provider=route.name,
        status="PASS" if valid else "FAIL",
        credentials="none",
        cost="$0",
        live_tested=True,
        useful_data=f"{count} listing records" if valid else "none",
        limitations=(
            "Technical development endpoint; not production authorization"
            if valid
            else "Unauthenticated route unavailable or returned an unexpected response"
        ),
        request_count=requests,
        status_code=response.status_code,
        content_type=response.content_type,
    )


def _probe_curated(client: BoundedHttpClient) -> CapabilityResult:
    source = CURATED_A2C_SOURCES[0]
    before = client.metrics.snapshot()
    try:
        response = client.get(
            post_feed_url(source.post_url),
            accept="application/atom+xml, application/xml;q=0.9",
        )
    except (ProviderHttpError, RequestLimitReached) as exc:
        requests, _, _ = metrics_delta(before, client.metrics.snapshot())
        return _failed_capability("curated_a2c", exc, request_count=requests)
    requests, _, _ = metrics_delta(before, client.metrics.snapshot())
    root, error = parse_atom_root(response.body, "curated_a2c")
    links: list[str] = []
    if response.status_code == 200 and error is None and root is not None:
        entry = next(
            (
                item
                for item in root.findall("atom:entry", ATOM)
                if (item.findtext("atom:id", default="", namespaces=ATOM)).startswith("t3_")
            ),
            None,
        )
        if entry is not None:
            content = entry.findtext("atom:content", default="", namespaces=ATOM)
            links = extract_curated_post_urls(
                content,
                exclude_urls=(source.post_url,),
            )
    success = bool(links)
    return CapabilityResult(
        provider="curated_a2c",
        status="PASS" if success else "FAIL",
        credentials="none",
        cost="$0",
        live_tested=True,
        useful_data=f"{len(links)} curated A2C post links" if success else "none",
        limitations=(
            "Historical masterpost; linked procedural advice still needs freshness review"
            if success
            else "Curated feed unavailable or contained no usable A2C links"
        ),
        request_count=requests,
        status_code=response.status_code,
        content_type=response.content_type,
    )


def _probe_rss(client: BoundedHttpClient) -> CapabilityResult:
    route = rss_routes("ApplyingToCollege")[0]
    before = client.metrics.snapshot()
    try:
        response = client.get(
            route.url,
            accept="application/atom+xml, application/xml;q=0.9",
        )
    except (ProviderHttpError, RequestLimitReached) as exc:
        requests, _, _ = metrics_delta(before, client.metrics.snapshot())
        return _failed_capability("rss_atom", exc, request_count=requests)
    requests, _, _ = metrics_delta(before, client.metrics.snapshot())
    candidates, errors = parse_atom_candidates(
        response.body,
        discovered_by=route.name,
        retrieved_at=utc_now(),
        feed_url=response.url,
        acquisition_method="rss_atom",
    ) if response.status_code == 200 else ([], [])
    success = bool(candidates) and not errors
    if response.status_code == 429:
        limitation = "Atom route is currently rate limited; no bypass or repeated retry was attempted"
    elif response.status_code != 200:
        limitation = f"Atom route returned HTTP {response.status_code}"
    elif success:
        limitation = "Limited feed depth and no engagement fields; development/calibration only"
    else:
        limitation = "Atom route returned malformed or unusable feed data"
    return CapabilityResult(
        provider="rss_atom",
        status="PASS" if success else "FAIL",
        credentials="none",
        cost="$0",
        live_tested=True,
        useful_data=f"{len(candidates)} post records" if success else "none",
        limitations=limitation,
        request_count=requests,
        status_code=response.status_code,
        content_type=response.content_type,
    )


def _probe_praw(*, environ: Mapping[str, str] | None) -> CapabilityResult:
    try:
        credentials = credentials_from_environment(
            load_env=environ is None,
            environ=environ,
        )
    except MissingRedditCredentials:
        return CapabilityResult(
            provider="praw_oauth",
            status="MISSING_CREDENTIALS",
            credentials="required",
            cost="$0 API cost / approval dependent",
            live_tested=False,
            useful_data="none",
            limitations="Set the three REDDIT_* variables after obtaining authorized access",
        )
    try:
        reddit = create_reddit_client(credentials)
        probe = probe_reddit_auth_with_retries(reddit)
    except RedditAuthError as exc:
        return _failed_capability("praw_oauth", exc, credentials="required")
    success = probe.success and probe.accessible and probe.read_only
    return CapabilityResult(
        provider="praw_oauth",
        status="PASS" if success else "FAIL",
        credentials="required",
        cost="$0 API cost / approval dependent",
        live_tested=True,
        useful_data="Authenticated post metadata and text" if success else "none",
        limitations=(
            "Production use remains subject to Reddit approval and terms"
            if success
            else probe.error or "OAuth access could not be confirmed"
        ),
        request_count=1,
    )


def _failed_capability(
    provider: str,
    error: Exception,
    *,
    request_count: int = 0,
    credentials: str = "none",
) -> CapabilityResult:
    return CapabilityResult(
        provider=provider,
        status="FAIL",
        credentials=credentials,
        cost="$0",
        live_tested=True,
        useful_data="none",
        limitations=str(error),
        request_count=request_count,
    )
