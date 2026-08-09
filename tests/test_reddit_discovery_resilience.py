from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.reddit_discovery.constants import DiscoveryRoute
from src.reddit_discovery.discovery import (
    candidate_from_submission,
    collect_comments_for_candidates,
    discover_candidates,
)
from src.reddit_discovery.retry import (
    is_retryable_reddit_error,
    run_with_retries,
)


class TooManyRequests(Exception):
    pass


class Forbidden(Exception):
    pass


class NotFound(Exception):
    pass


class OAuthException(Exception):
    pass


class ResponseException(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.response = SimpleNamespace(status_code=status_code, headers={})


class _Reddit:
    def __init__(self, subreddit: object) -> None:
        self._subreddit = subreddit

    def subreddit(self, name: str) -> object:
        assert name == "ApplyingToCollege"
        return self._subreddit


def _submission(post_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=post_id,
        name=f"t3_{post_id}",
        subreddit=SimpleNamespace(display_name="ApplyingToCollege"),
        title=f"Guide {post_id}",
        selftext="A detailed and durable application guide.",
        permalink=f"/r/ApplyingToCollege/comments/{post_id}/guide/",
        url=f"https://www.reddit.com/r/ApplyingToCollege/comments/{post_id}/guide/",
        author=SimpleNamespace(name="guide-author"),
        created_utc=1_700_000_000,
        score=100,
        upvote_ratio=0.95,
        num_comments=20,
        link_flair_text="Advice",
        is_self=True,
        is_original_content=True,
        over_18=False,
        spoiler=False,
        stickied=False,
        distinguished=None,
        locked=False,
        archived=False,
        removed_by_category=None,
    )


def _hot_route() -> DiscoveryRoute:
    return DiscoveryRoute("listing", "hot", 25, listing="hot")


def test_lazy_route_retains_partial_post_and_remains_checkpointable() -> None:
    attempts = 0

    class PartialFailureSubreddit:
        def hot(self, *, limit: int):
            nonlocal attempts
            attempts += 1
            assert limit == 25
            yield _submission("partial")
            raise TooManyRequests("temporary failure")

    snapshots: list[dict[str, object]] = []

    def checkpoint(result, route) -> None:
        snapshots.append(
            {
                "candidate_ids": [item.reddit_post_id for item in result.candidates],
                "completed_routes": list(result.completed_routes),
                "error_routes": [error.route for error in result.errors],
                "route": route.origin,
            }
        )

    result = discover_candidates(
        _Reddit(PartialFailureSubreddit()),
        routes=(_hot_route(),),
        max_retries=2,
        backoff_base_seconds=0,
        sleep_fn=lambda _: None,
        checkpoint_callback=checkpoint,
    )

    assert attempts == 3
    assert [item.reddit_post_id for item in result.candidates] == ["partial"]
    assert result.route_counts == {"hot": 1}
    assert result.completed_routes == []
    assert len(result.errors) == 1
    assert result.errors[0].route == "hot"
    assert result.errors[0].retryable is True
    assert result.errors[0].attempts == 3
    assert snapshots == [
        {
            "candidate_ids": ["partial"],
            "completed_routes": [],
            "error_routes": ["hot"],
            "route": "hot",
        }
    ]


def test_successful_retry_dedupes_items_yielded_before_restart() -> None:
    attempts = 0

    class RestartingSubreddit:
        def hot(self, *, limit: int):
            nonlocal attempts
            attempts += 1
            assert limit == 25
            yield _submission("same")
            if attempts == 1:
                raise TooManyRequests("restart listing")
            yield _submission("new")

    result = discover_candidates(
        _Reddit(RestartingSubreddit()),
        routes=(_hot_route(),),
        max_retries=2,
        backoff_base_seconds=0,
        sleep_fn=lambda _: None,
    )

    assert attempts == 2
    assert [item.reddit_post_id for item in result.candidates] == ["same", "new"]
    assert result.route_counts == {"hot": 2}
    assert result.completed_routes == ["hot"]
    assert result.errors == []


def test_response_401_is_permanent_and_does_not_retry() -> None:
    attempts = 0
    sleeps: list[float] = []

    def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise ResponseException(401)

    value, error = run_with_retries(
        operation,
        route="hot",
        max_retries=3,
        backoff_base_seconds=1,
        sleep_fn=sleeps.append,
    )

    assert attempts == 1
    assert sleeps == []
    assert value is None
    assert error is not None
    assert error.retryable is False
    assert error.attempts == 1
    assert is_retryable_reddit_error(ResponseException(401)) is False


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_response_429_and_5xx_may_retry(status_code: int) -> None:
    attempts = 0

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ResponseException(status_code)
        return "ok"

    value, error = run_with_retries(
        operation,
        route="hot",
        max_retries=2,
        backoff_base_seconds=0,
        sleep_fn=lambda _: None,
    )

    assert attempts == 3
    assert value == "ok"
    assert error is None
    assert is_retryable_reddit_error(ResponseException(status_code)) is True


def test_json_decode_error_uses_bounded_retries() -> None:
    attempts = 0

    def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise json.JSONDecodeError("invalid response", "{", 1)

    value, error = run_with_retries(
        operation,
        route="hot",
        max_retries=2,
        backoff_base_seconds=0,
        sleep_fn=lambda _: None,
    )

    assert attempts == 3
    assert value is None
    assert error is not None
    assert error.error_type == "JSONDecodeError"
    assert error.retryable is True
    assert error.attempts == 3


def test_exhausted_global_rate_limit_stops_route_fanout() -> None:
    calls: list[str] = []

    class RateLimitedSubreddit:
        def hot(self, *, limit: int):
            calls.append("hot")
            raise TooManyRequests("global rate limit")

        def new(self, *, limit: int):
            calls.append("new")
            return []

    result = discover_candidates(
        _Reddit(RateLimitedSubreddit()),
        routes=(
            _hot_route(),
            DiscoveryRoute("listing", "new", 25, listing="new"),
        ),
        max_retries=1,
        backoff_base_seconds=0,
        sleep_fn=lambda _: None,
    )

    assert calls == ["hot", "hot"]
    assert result.completed_routes == []
    assert list(result.route_counts) == ["hot"]
    assert result.errors[0].error_type == "TooManyRequests"


def test_permanent_auth_error_stops_route_fanout() -> None:
    calls: list[str] = []

    class UnauthorizedSubreddit:
        def hot(self, *, limit: int):
            calls.append("hot")
            raise ResponseException(401)

        def new(self, *, limit: int):
            calls.append("new")
            return []

    result = discover_candidates(
        _Reddit(UnauthorizedSubreddit()),
        routes=(
            _hot_route(),
            DiscoveryRoute("listing", "new", 25, listing="new"),
        ),
        max_retries=3,
        backoff_base_seconds=0,
        sleep_fn=lambda _: None,
    )

    assert calls == ["hot"]
    assert result.errors[0].retryable is False


def test_global_comment_failure_marks_remaining_posts_unattempted() -> None:
    candidates = [
        candidate_from_submission(_submission(post_id), "top:year")
        for post_id in ("first", "second")
    ]

    class GlobalFailureCommentsReddit:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def submission(self, *, id: str) -> object:
            self.calls.append(id)
            raise OAuthException("access revoked")

    reddit = GlobalFailureCommentsReddit()
    errors = collect_comments_for_candidates(reddit, candidates, max_retries=0)

    assert reddit.calls == ["first"]
    assert [error.route for error in errors] == ["comments:first", "comments:second"]
    assert [error.attempts for error in errors] == [1, 0]


@pytest.mark.parametrize("access_error", [NotFound, Forbidden])
def test_post_specific_access_error_does_not_stop_later_comments(
    access_error: type[Exception],
) -> None:
    candidates = [
        candidate_from_submission(_submission(post_id), "top:year")
        for post_id in ("deleted", "available")
    ]

    class EmptyComments(list[object]):
        def replace_more(self, *, limit: int) -> None:
            assert limit == 0

    class CommentsReddit:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def submission(self, *, id: str) -> object:
            self.calls.append(id)
            if id == "deleted":
                raise access_error("post disappeared")
            return SimpleNamespace(comments=EmptyComments())

    reddit = CommentsReddit()
    errors = collect_comments_for_candidates(reddit, candidates, max_retries=0)

    assert reddit.calls == ["deleted", "available"]
    assert [error.route for error in errors] == ["comments:deleted"]


def test_rejected_search_combination_is_terminal_but_auditable() -> None:
    calls: list[str] = []

    class BadRequest(ResponseException):
        pass

    class SearchRejectingSubreddit:
        def search(self, query: str, **kwargs: object):
            calls.append("search")
            raise BadRequest(400)

        def new(self, *, limit: int):
            calls.append("new")
            return []

    search = DiscoveryRoute(
        "search",
        "search:test:query:top:all",
        250,
        query_group="test",
        query="query",
        sort="top",
        time_filter="all",
    )
    result = discover_candidates(
        _Reddit(SearchRejectingSubreddit()),
        routes=(search, DiscoveryRoute("listing", "new", 25, listing="new")),
        max_retries=1,
        backoff_base_seconds=0,
        sleep_fn=lambda _: None,
    )

    assert calls == ["search", "new"]
    assert result.completed_routes == [search.origin, "new"]
    assert len(result.errors) == 1
    assert result.errors[0].error_type == "BadRequest"
