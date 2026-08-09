from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from src.processing.cleaner import clean_text, clean_title
from src.reddit_discovery.constants import (
    DEFAULT_MAX_COMMENTS_PER_POST,
    DEFAULT_MAX_RETRIES,
    DiscoveryRoute,
    SUPPORTED_SUBREDDIT,
    build_discovery_routes,
)
from src.reddit_discovery.models import (
    DiscoveryError,
    DiscoveryResult,
    RedditCandidate,
    RedditComment,
    utc_timestamp_to_iso,
)
from src.reddit_discovery.retry import (
    SleepFunction,
    collect_iterable_with_retries,
    run_with_retries,
    should_abort_after_reddit_error,
)
from src.time_utils import utc_now


REDDIT_BASE_URL = "https://www.reddit.com"
_REACTION_ONLY_RE = re.compile(
    r"^(?:lol|lmao|lmfao|same|this|facts|real|based|nice|wow|congrats|good luck|"
    r"you got this|i agree|exactly|thank you|thanks)[!?.\s\W]*$",
    re.IGNORECASE,
)
_TRACKING_PARAMETERS = {"fbclid", "gclid", "ref", "ref_source", "source", "mc_cid", "mc_eid"}

CheckpointCallback = Callable[[DiscoveryResult, DiscoveryRoute], None]


def discover_candidates(
    reddit: Any,
    *,
    subreddit_name: str = SUPPORTED_SUBREDDIT,
    max_range: bool = False,
    quick: bool = False,
    routes: Sequence[DiscoveryRoute] | None = None,
    candidate_limit: int | None = None,
    retrieved_at: str | None = None,
    seed_candidates: Iterable[RedditCandidate] = (),
    completed_routes: Iterable[str] = (),
    checkpoint_callback: CheckpointCallback | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_base_seconds: float = 1.0,
    sleep_fn: SleepFunction = time.sleep,
) -> DiscoveryResult:
    subreddit_name = validate_subreddit_name(subreddit_name)
    if max_range and quick:
        raise ValueError("--max-range and --quick are mutually exclusive.")
    if candidate_limit is not None and candidate_limit <= 0:
        raise ValueError("candidate_limit must be a positive integer when provided.")
    route_specs = tuple(
        routes
        if routes is not None
        else build_discovery_routes(max_range=max_range, quick=quick)
    )
    retrieved_at = retrieved_at or utc_now()
    completed_route_list = list(dict.fromkeys(completed_routes))
    previously_completed = set(completed_route_list)
    index = _CandidateIndex(dedupe_urls=False)
    for candidate in seed_candidates:
        index.add(candidate)
    result = DiscoveryResult(
        candidates=index.candidates,
        duplicates=index.duplicates,
        completed_routes=completed_route_list,
    )
    subreddit = reddit.subreddit(subreddit_name)

    pending_routes = [route for route in route_specs if route.origin not in previously_completed]
    for route_index, route in enumerate(pending_routes):
        route_quota: int | None = None
        if candidate_limit is not None:
            slots = max(0, candidate_limit - len(index.candidates))
            if slots == 0:
                result.limit_reached = True
                break
            routes_left = len(pending_routes) - route_index
            route_quota = (slots + routes_left - 1) // routes_left

        request_route = (
            replace(route, limit=min(route.limit, route_quota))
            if route_quota is not None
            else route
        )

        submissions, error = collect_iterable_with_retries(
            lambda route=request_route: iter_route(subreddit, route),
            route=route.origin,
            max_retries=max_retries,
            backoff_base_seconds=backoff_base_seconds,
            sleep_fn=sleep_fn,
            item_key=lambda submission: str(getattr(submission, "id", "")) or id(submission),
        )
        route_count = 0
        new_candidates = 0
        for submission in submissions:
            try:
                candidate = candidate_from_submission(submission, route.origin, retrieved_at)
            except Exception as exc:
                result.errors.append(
                    DiscoveryError(
                        route=f"{route.origin}:submission",
                        error_type=type(exc).__name__,
                        message=(
                            "Could not map a Reddit submission: "
                            f"{type(exc).__name__}."
                        ),
                        retryable=False,
                        attempts=1,
                    )
                )
                continue
            route_count += 1
            before_count = len(index.candidates)
            index.add(
                candidate,
                allow_new=route_quota is None or new_candidates < route_quota,
            )
            if len(index.candidates) > before_count:
                new_candidates += 1
        if error is None or _is_tolerated_search_rejection(route, error):
            result.completed_routes.append(route.origin)
        if error is not None:
            result.errors.append(error)
        result.route_counts[route.origin] = route_count
        _run_checkpoint_callback(checkpoint_callback, result, route)
        if error is not None and should_abort_after_reddit_error(error):
            break
        if candidate_limit is not None and len(index.candidates) >= candidate_limit:
            result.limit_reached = True
            break

    return result


def iter_route(subreddit: Any, route: DiscoveryRoute) -> Iterator[Any]:
    if route.kind == "search":
        return iter(
            subreddit.search(
                route.query,
                sort=route.sort,
                time_filter=route.time_filter,
                syntax="lucene",
                limit=route.limit,
            )
        )
    if route.kind != "listing":
        raise ValueError(f"Unsupported Reddit discovery route kind: {route.kind}")

    listing_method = getattr(subreddit, route.listing)
    if route.listing == "top":
        return iter(listing_method(time_filter=route.time_filter, limit=route.limit))
    return iter(listing_method(limit=route.limit))


def _is_tolerated_search_rejection(
    route: DiscoveryRoute,
    error: DiscoveryError,
) -> bool:
    if route.kind != "search" or error.retryable:
        return False
    if error.error_type == "BadRequest":
        return True
    return error.error_type == "ResponseException" and "HTTP 400" in error.message


def candidate_from_submission(
    submission: Any,
    discovered_by: str,
    retrieved_at: str | None = None,
) -> RedditCandidate:
    post_id = _text(getattr(submission, "id", ""))
    permalink = absolute_reddit_url(_text(getattr(submission, "permalink", "")))
    if not permalink and post_id:
        permalink = canonicalize_url(f"{REDDIT_BASE_URL}/comments/{post_id}")
    submission_url = canonicalize_url(_text(getattr(submission, "url", "")))
    external_url = submission_url if _is_external_url(submission_url) else None
    canonical_url = permalink or submission_url
    author = getattr(submission, "author", None)
    subreddit = getattr(submission, "subreddit", None)
    subreddit_name = _text(getattr(subreddit, "display_name", "") or subreddit)

    return RedditCandidate(
        reddit_post_id=post_id,
        fullname=_text(getattr(submission, "name", "")) or (f"t3_{post_id}" if post_id else ""),
        subreddit=subreddit_name,
        title=clean_title(_text(getattr(submission, "title", ""))),
        selftext=clean_text(_text(getattr(submission, "selftext", ""))),
        canonical_url=canonical_url,
        permalink=permalink or canonical_url,
        author_name=_author_name(author),
        created_utc=_optional_float(getattr(submission, "created_utc", None)),
        retrieved_at=retrieved_at or utc_now(),
        score=_integer(getattr(submission, "score", 0)),
        upvote_ratio=_optional_float(getattr(submission, "upvote_ratio", None)),
        num_comments=_integer(getattr(submission, "num_comments", 0)),
        link_flair_text=_optional_text(getattr(submission, "link_flair_text", None)),
        is_self=bool(getattr(submission, "is_self", False)),
        is_original_content=bool(getattr(submission, "is_original_content", False)),
        over_18=bool(getattr(submission, "over_18", False)),
        spoiler=bool(getattr(submission, "spoiler", False)),
        stickied=bool(getattr(submission, "stickied", False)),
        distinguished=_optional_text(getattr(submission, "distinguished", None)),
        locked=bool(getattr(submission, "locked", False)),
        archived=bool(getattr(submission, "archived", False)),
        removed_by_category=_optional_text(getattr(submission, "removed_by_category", None)),
        acquisition_method="reddit_api",
        provenance={"collector": "praw"},
        discovered_by=[discovered_by],
        external_url=external_url,
    )


def merge_candidates_by_id_and_url(
    candidates: Iterable[RedditCandidate],
) -> tuple[list[RedditCandidate], list[RedditCandidate]]:
    index = _CandidateIndex()
    for candidate in candidates:
        index.add(candidate)
    return index.candidates, index.duplicates


def collect_comments_for_candidates(
    reddit: Any,
    candidates: Iterable[RedditCandidate],
    *,
    max_comments_per_post: int = DEFAULT_MAX_COMMENTS_PER_POST,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_base_seconds: float = 1.0,
    sleep_fn: SleepFunction = time.sleep,
) -> list[DiscoveryError]:
    if max_comments_per_post < 0:
        raise ValueError("max_comments_per_post cannot be negative.")
    errors: list[DiscoveryError] = []
    values = list(candidates)
    for index, candidate in enumerate(values):
        if max_comments_per_post == 0:
            candidate.comments = []
            continue
        comments, error = run_with_retries(
            lambda candidate=candidate: collect_submission_comments(
                reddit.submission(id=candidate.reddit_post_id),
                max_comments=max_comments_per_post,
            ),
            route=f"comments:{candidate.reddit_post_id}",
            max_retries=max_retries,
            backoff_base_seconds=backoff_base_seconds,
            sleep_fn=sleep_fn,
        )
        if comments is not None:
            candidate.comments = comments
        if error is not None:
            errors.append(error)
            if should_abort_after_reddit_error(
                error,
                not_found_is_global=False,
                forbidden_is_global=False,
            ):
                errors.extend(
                    DiscoveryError(
                        route=f"comments:{remaining.reddit_post_id}",
                        error_type=error.error_type,
                        message=(
                            "Comment collection skipped after an exhausted global "
                            f"Reddit API failure: {error.error_type}."
                        ),
                        retryable=error.retryable,
                        attempts=0,
                    )
                    for remaining in values[index + 1 :]
                )
                break
    return errors


def collect_submission_comments(
    submission: Any,
    *,
    max_comments: int = DEFAULT_MAX_COMMENTS_PER_POST,
) -> list[RedditComment]:
    if max_comments <= 0:
        return []
    submission.comment_sort = "top"
    submission.comments.replace_more(limit=0)
    comments: list[RedditComment] = []
    for comment in submission.comments:
        body = clean_text(_text(getattr(comment, "body", "")))
        author = _author_name(getattr(comment, "author", None))
        if not is_useful_comment(body, author):
            continue
        comments.append(
            RedditComment(
                comment_id=_text(getattr(comment, "id", "")),
                author=author,
                body=body,
                score=_integer(getattr(comment, "score", 0)),
                created_at=utc_timestamp_to_iso(
                    _optional_float(getattr(comment, "created_utc", None))
                ),
                permalink=absolute_reddit_url(_text(getattr(comment, "permalink", ""))),
            )
        )
        if len(comments) >= max_comments:
            break
    return comments


def is_useful_comment(body: str, author: str | None) -> bool:
    compact = clean_text(body)
    if not compact or compact.lower() in {"[deleted]", "[removed]"}:
        return False
    if author and author.casefold() == "automoderator":
        return False
    if len(compact) < 80:
        return False
    if _REACTION_ONLY_RE.fullmatch(compact):
        return False
    if compact.lower().endswith("/s") and len(compact.split()) < 30:
        return False
    return True


def validate_subreddit_name(value: str) -> str:
    name = value.strip()
    if name.casefold() != SUPPORTED_SUBREDDIT.casefold():
        raise ValueError(f"Only r/{SUPPORTED_SUBREDDIT} is supported by this pipeline.")
    return SUPPORTED_SUBREDDIT


def canonicalize_url(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    hostname = (parsed.hostname or "").lower()
    if hostname == "reddit.com" or hostname.endswith(".reddit.com"):
        netloc = "www.reddit.com"
    else:
        default_port = parsed.port in {None, 80, 443}
        netloc = hostname if default_port else f"{hostname}:{parsed.port}"
    path = re.sub(r"/{2,}", "/", parsed.path).rstrip("/") or "/"
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in _TRACKING_PARAMETERS
    ]
    return urlunparse(("https", netloc, path, "", urlencode(sorted(query)), ""))


def absolute_reddit_url(value: str) -> str:
    text = value.strip()
    if text.startswith("/"):
        return canonicalize_url(f"{REDDIT_BASE_URL}{text}")
    return canonicalize_url(text)


def _is_external_url(value: str) -> bool:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    return bool(parsed.scheme in {"http", "https"} and hostname) and not (
        hostname == "reddit.com" or hostname.endswith(".reddit.com")
    )


class _CandidateIndex:
    def __init__(
        self, candidates: Iterable[RedditCandidate] = (),
        duplicates: Iterable[RedditCandidate] = (), *, dedupe_urls: bool = True,
    ) -> None:
        self._dedupe_urls = dedupe_urls
        self.candidates: list[RedditCandidate] = []
        self.duplicates = list(duplicates)
        self._by_id: dict[str, RedditCandidate] = {}
        self._by_url: dict[str, RedditCandidate] = {}
        for candidate in candidates:
            self.add(candidate)

    def add(
        self, candidate: RedditCandidate, *, allow_new: bool = True,
    ) -> RedditCandidate | None:
        post_key = candidate.reddit_post_id.strip().lower()
        url_key = canonicalize_url(candidate.canonical_url).lower()
        existing = self._by_id.get(post_key) if post_key else None
        if existing is not None:
            _merge_same_post(existing, candidate)
            return existing
        existing = self._by_url.get(url_key) if self._dedupe_urls and url_key else None
        if existing is not None:
            candidate.rejection_reason = "DUPLICATE_CANONICAL_URL"
            candidate.duplicate_of = existing.reddit_post_id or existing.canonical_url
            candidate.processing_status = "rejected"
            self.duplicates.append(candidate)
            if post_key:
                self._by_id[post_key] = candidate
            return existing
        if not allow_new:
            return None
        self.candidates.append(candidate)
        if post_key:
            self._by_id[post_key] = candidate
        if self._dedupe_urls and url_key:
            self._by_url[url_key] = candidate
        return candidate


def _merge_same_post(existing: RedditCandidate, incoming: RedditCandidate) -> None:
    existing.merge_discovered_by(incoming.discovered_by)
    existing_unavailable = _candidate_is_unavailable(existing)
    incoming_unavailable = _candidate_is_unavailable(incoming)
    if incoming_unavailable:
        existing.selftext = incoming.selftext
    elif not existing_unavailable and len(incoming.selftext) > len(existing.selftext):
        existing.selftext = incoming.selftext
    for field_name in (
        "fullname", "subreddit", "title", "canonical_url", "permalink",
        "author_name", "created_utc", "retrieved_at", "score", "upvote_ratio",
        "num_comments", "link_flair_text", "is_self", "is_original_content",
        "over_18", "spoiler", "stickied", "distinguished", "locked", "archived",
        "external_url", "acquisition_method", "provenance",
    ):
        setattr(existing, field_name, getattr(incoming, field_name))
    if incoming_unavailable or not existing_unavailable:
        existing.removed_by_category = incoming.removed_by_category
    existing.refresh_content_hash()


def _candidate_is_unavailable(candidate: RedditCandidate) -> bool:
    body = candidate.selftext.strip().casefold()
    removed = (candidate.removed_by_category or "").strip().casefold()
    return body in {"[deleted]", "[removed]"} or removed not in {"", "none"}


def _run_checkpoint_callback(callback: CheckpointCallback | None, result: DiscoveryResult, route: DiscoveryRoute) -> None:
    if callback is None:
        return
    try:
        callback(result, route)
    except (OSError, ValueError, TypeError) as exc:
        result.errors.append(
            DiscoveryError(
                route=f"checkpoint:{route.origin}",
                error_type=type(exc).__name__,
                message=f"Checkpoint failed: {type(exc).__name__}.",
                retryable=False,
                attempts=1,
            )
        )


def _author_name(author: Any) -> str | None:
    if author is None:
        return None
    return _optional_text(getattr(author, "name", None) or author)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _optional_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
