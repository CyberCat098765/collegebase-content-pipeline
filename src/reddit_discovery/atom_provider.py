from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable

from bs4 import BeautifulSoup

from src.reddit_discovery.discovery import merge_candidates_by_id_and_url
from src.reddit_discovery.models import DiscoveryError, DiscoveryResult, RedditCandidate
from src.reddit_discovery.provider_http import (
    BoundedHttpClient,
    HttpResponse,
    ProviderHttpError,
    RequestLimitReached,
)
from src.reddit_discovery.provider_normalization import (
    ATOM,
    candidate_from_atom_entry,
    parse_reddit_post_url,
)
from src.time_utils import utc_now


@dataclass(frozen=True, slots=True)
class AtomRoute:
    name: str
    url: str


@dataclass(frozen=True, slots=True)
class CuratedSource:
    name: str
    post_url: str


CURATED_A2C_SOURCES = (
    CuratedSource(
        "organized_masterpost",
        "https://www.reddit.com/r/ApplyingToCollege/comments/imxhiu/",
    ),
)


def rss_routes(subreddit: str) -> tuple[AtomRoute, ...]:
    base = f"https://www.reddit.com/r/{subreddit}"
    return (
        AtomRoute("rss_top_year", f"{base}/top/.rss?t=year"),
        AtomRoute("rss_new", f"{base}/.rss"),
    )


def acquire_rss(
    client: BoundedHttpClient,
    *,
    subreddit: str,
    candidate_limit: int,
    routes: Iterable[AtomRoute] | None = None,
    retrieved_at: str | None = None,
) -> DiscoveryResult:
    acquired: list[RedditCandidate] = []
    errors: list[DiscoveryError] = []
    route_counts: dict[str, int] = {}
    completed: list[str] = []
    retrieved = retrieved_at or utc_now()

    for route in routes or rss_routes(subreddit):
        response, error = _get_atom(client, route.name, route.url)
        if error is not None:
            errors.append(error)
            route_counts[route.name] = 0
            if error.status_code in {403, 429} or error.error_type == "RequestLimitReached":
                break
            continue
        assert response is not None
        candidates, parse_errors = parse_atom_candidates(
            response.body,
            discovered_by=route.name,
            retrieved_at=retrieved,
            feed_url=response.url,
            acquisition_method="rss_atom",
        )
        remaining = max(0, candidate_limit - len(acquired))
        accepted = candidates[:remaining]
        acquired.extend(accepted)
        errors.extend(parse_errors)
        route_counts[route.name] = len(accepted)
        completed.append(route.name)
        if len(acquired) >= candidate_limit:
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


def acquire_curated_a2c(
    client: BoundedHttpClient,
    *,
    candidate_limit: int,
    detail_limit: int,
    sources: Iterable[CuratedSource] = CURATED_A2C_SOURCES,
    retrieved_at: str | None = None,
) -> DiscoveryResult:
    acquired: list[RedditCandidate] = []
    errors: list[DiscoveryError] = []
    route_counts: dict[str, int] = {}
    completed: list[str] = []
    retrieved = retrieved_at or utc_now()

    for source in sources:
        route_name = f"curated_a2c:{source.name}"
        feed_url = post_feed_url(source.post_url)
        response, error = _get_atom(client, route_name, feed_url)
        if error is not None:
            errors.append(error)
            route_counts[route_name] = 0
            continue
        assert response is not None
        root, parse_error = parse_atom_root(response.body, route_name)
        if parse_error is not None:
            errors.append(parse_error)
            route_counts[route_name] = 0
            continue
        assert root is not None
        source_entry = next(
            (
                entry
                for entry in root.findall("atom:entry", ATOM)
                if _atom_text(entry, "id").startswith("t3_")
            ),
            None,
        )
        if source_entry is None:
            errors.append(
                _parse_error(route_name, "Curated Atom feed has no Reddit post entry.")
            )
            route_counts[route_name] = 0
            continue

        source_added = False
        try:
            source_candidate = candidate_from_atom_entry(
                source_entry,
                discovered_by=route_name,
                retrieved_at=retrieved,
                feed_url=response.url,
                acquisition_method="curated_a2c",
                extra_provenance={
                    "curated_source": source.name,
                    "curated_source_url": source.post_url,
                },
            )
        except ValueError as exc:
            errors.append(_parse_error(route_name, str(exc)))
        else:
            acquired.append(source_candidate)
            source_added = True

        links = extract_curated_post_urls(
            _atom_text(source_entry, "content"),
            exclude_urls=(source.post_url,),
        )
        selected_links = evenly_spaced(links, min(detail_limit, candidate_limit - len(acquired)))
        fetched = 1 if source_added else 0
        interrupted = False
        for index, post_url in enumerate(selected_links, start=1):
            if len(acquired) >= candidate_limit:
                break
            detail_route = f"{route_name}:detail-{index}"
            detail_response, detail_error = _get_atom(
                client,
                detail_route,
                post_feed_url(post_url),
            )
            if detail_error is not None:
                errors.append(detail_error)
                if detail_error.status_code in {403, 429} or detail_error.error_type == "RequestLimitReached":
                    interrupted = True
                    break
                continue
            assert detail_response is not None
            candidates, detail_errors = parse_atom_candidates(
                detail_response.body,
                discovered_by=route_name,
                retrieved_at=retrieved,
                feed_url=detail_response.url,
                acquisition_method="curated_a2c",
                extra_provenance={
                    "curated_source": source.name,
                    "curated_source_url": source.post_url,
                    "curated_post_url": post_url,
                },
            )
            errors.extend(detail_errors)
            if candidates:
                acquired.append(candidates[0])
                fetched += 1
        route_counts[route_name] = fetched
        if not interrupted:
            completed.append(route_name)

    unique, duplicates = merge_candidates_by_id_and_url(acquired)
    return DiscoveryResult(
        candidates=unique,
        duplicates=duplicates,
        errors=errors,
        route_counts=route_counts,
        completed_routes=completed,
        limit_reached=len(unique) >= candidate_limit,
    )


def acquire_manual_urls(
    client: BoundedHttpClient,
    *,
    urls: Iterable[str],
    candidate_limit: int,
    retrieved_at: str | None = None,
) -> DiscoveryResult:
    acquired: list[RedditCandidate] = []
    errors: list[DiscoveryError] = []
    seen: set[str] = set()
    retrieved = retrieved_at or utc_now()

    for index, raw_url in enumerate(urls, start=1):
        parsed = parse_reddit_post_url(raw_url)
        route = f"manual_url:{index}"
        if parsed is None or parsed[0].casefold() != "applyingtocollege":
            errors.append(_parse_error(route, "Manual URL must be an r/ApplyingToCollege post."))
            continue
        post_url = parsed[2]
        if post_url in seen:
            continue
        seen.add(post_url)
        response, error = _get_atom(client, route, post_feed_url(post_url))
        if error is not None:
            errors.append(error)
            if error.status_code in {403, 429} or error.error_type == "RequestLimitReached":
                break
            continue
        assert response is not None
        candidates, parse_errors = parse_atom_candidates(
            response.body,
            discovered_by=route,
            retrieved_at=retrieved,
            feed_url=response.url,
            acquisition_method="manual_url",
            extra_provenance={"manual_source_url": post_url},
        )
        errors.extend(parse_errors)
        if candidates:
            acquired.append(candidates[0])
        if len(acquired) >= candidate_limit:
            break

    unique, duplicates = merge_candidates_by_id_and_url(acquired)
    return DiscoveryResult(
        candidates=unique,
        duplicates=duplicates,
        errors=errors,
        route_counts={"manual_urls": len(acquired)},
        completed_routes=["manual_urls"],
        limit_reached=len(unique) >= candidate_limit,
    )


def parse_atom_candidates(
    body: str,
    *,
    discovered_by: str,
    retrieved_at: str,
    feed_url: str,
    acquisition_method: str,
    extra_provenance: dict[str, object] | None = None,
) -> tuple[list[RedditCandidate], list[DiscoveryError]]:
    root, error = parse_atom_root(body, discovered_by)
    if error is not None:
        return [], [error]
    assert root is not None
    candidates: list[RedditCandidate] = []
    errors: list[DiscoveryError] = []
    seen: set[str] = set()
    for index, entry in enumerate(root.findall("atom:entry", ATOM), start=1):
        if not _atom_text(entry, "id").startswith("t3_"):
            continue
        try:
            candidate = candidate_from_atom_entry(
                entry,
                discovered_by=discovered_by,
                retrieved_at=retrieved_at,
                feed_url=feed_url,
                acquisition_method=acquisition_method,
                extra_provenance=extra_provenance,
            )
        except ValueError as exc:
            errors.append(_parse_error(f"{discovered_by}:entry-{index}", str(exc)))
            continue
        if candidate.reddit_post_id in seen:
            continue
        seen.add(candidate.reddit_post_id)
        candidates.append(candidate)
    return candidates, errors


def parse_atom_root(
    body: str,
    route: str,
) -> tuple[ET.Element | None, DiscoveryError | None]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None, _parse_error(route, "Reddit Atom response contains malformed XML.")
    if not root.tag.endswith("feed"):
        return None, _parse_error(route, "Reddit Atom response is missing the feed root.")
    return root, None


def extract_curated_post_urls(
    content_html: str,
    *,
    exclude_urls: Iterable[str] = (),
) -> list[str]:
    soup = BeautifulSoup(content_html, "html.parser")
    urls: list[str] = []
    seen_ids = {
        parsed[1].casefold()
        for value in exclude_urls
        if (parsed := parse_reddit_post_url(value)) is not None
    }
    for anchor in soup.select("a[href]"):
        parsed = parse_reddit_post_url(str(anchor.get("href", "")))
        if parsed is None or parsed[0].casefold() != "applyingtocollege":
            continue
        post_id = parsed[1].casefold()
        if post_id in seen_ids:
            continue
        seen_ids.add(post_id)
        urls.append(parsed[2])
    return urls


def evenly_spaced(values: list[str], limit: int) -> list[str]:
    if limit <= 0:
        return []
    if len(values) <= limit:
        return list(values)
    return [values[index * len(values) // limit] for index in range(limit)]


def post_feed_url(post_url: str) -> str:
    parsed = parse_reddit_post_url(post_url)
    if parsed is None:
        raise ValueError("Expected a Reddit post URL.")
    return f"{parsed[2].rstrip('/')}/.rss"


def _get_atom(
    client: BoundedHttpClient,
    route: str,
    url: str,
) -> tuple[HttpResponse | None, DiscoveryError | None]:
    try:
        response = client.get(url, accept="application/atom+xml, application/xml;q=0.9")
    except (ProviderHttpError, RequestLimitReached) as exc:
        return None, DiscoveryError(
            route=route,
            error_type=type(exc).__name__,
            message=str(exc),
            retryable=bool(getattr(exc, "retryable", False)),
            attempts=int(getattr(exc, "attempts", 0)),
        )
    if response.status_code != 200:
        return None, DiscoveryError(
            route=route,
            error_type=f"HTTP{response.status_code}",
            message=(
                "Reddit Atom access remained rate limited after bounded retry."
                if response.status_code == 429
                else f"Reddit Atom returned HTTP {response.status_code}."
            ),
            retryable=response.status_code == 429 or 500 <= response.status_code < 600,
            attempts=response.attempts,
            status_code=response.status_code,
        )
    if not any(marker in response.content_type.casefold() for marker in ("atom", "xml")):
        return None, DiscoveryError(
            route=route,
            error_type="UnexpectedContentType",
            message="Reddit Atom route returned a non-XML response.",
            retryable=False,
            attempts=response.attempts,
            status_code=response.status_code,
        )
    return response, None


def _atom_text(entry: ET.Element, name: str) -> str:
    element = entry.find(f"atom:{name}", ATOM)
    return "" if element is None or element.text is None else element.text.strip()


def _parse_error(route: str, message: str) -> DiscoveryError:
    return DiscoveryError(
        route=route,
        error_type="AtomParseError",
        message=message,
        retryable=False,
        attempts=1,
    )
