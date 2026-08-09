from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import requests

from src.reddit_discovery.filtering import RejectionCode, evaluate_hard_filters
from src.reddit_discovery.provider_http import BoundedHttpClient, RequestLimitReached
from src.reddit_discovery.public_json_provider import (
    PUBLIC_JSON_ROUTES,
    acquire_public_json,
)


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        body: str,
        *,
        content_type: str = "application/json",
        headers: dict[str, str] | None = None,
        url: str = "https://www.reddit.com/test",
    ) -> None:
        self.status_code = status_code
        self.text = body
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.url = url


class FakeSession:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if not self.responses:
            raise AssertionError("Unexpected HTTP request")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        response.url = url
        return response


def test_public_json_normalizes_fields_and_preserves_missing_metadata() -> None:
    record = _post("abc123")
    record.pop("author")
    record.pop("score")
    record.pop("upvote_ratio")
    record.pop("num_comments")
    session = FakeSession([_listing_response([record])])
    result = acquire_public_json(
        _client(session),
        subreddit="ApplyingToCollege",
        candidate_limit=5,
        routes=PUBLIC_JSON_ROUTES[:1],
        retrieved_at="2026-08-09T00:00:00Z",
    )

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.reddit_post_id == "abc123"
    assert candidate.author_name is None
    assert candidate.score is None
    assert candidate.upvote_ratio is None
    assert candidate.num_comments is None
    assert candidate.canonical_url.startswith(
        "https://www.reddit.com/r/ApplyingToCollege/comments/abc123"
    )
    assert candidate.provenance["provider"] == "public_json"
    assert set(candidate.provenance["missing_optional_fields"]) == {
        "author",
        "score",
        "upvote_ratio",
        "num_comments",
    }


def test_public_json_paginates_until_after_is_missing() -> None:
    session = FakeSession(
        [
            _listing_response([_post("one")], after="t3_one"),
            _listing_response([_post("two")]),
        ]
    )
    result = acquire_public_json(
        _client(session),
        subreddit="ApplyingToCollege",
        candidate_limit=10,
        max_pages_per_route=3,
        routes=PUBLIC_JSON_ROUTES[:1],
    )

    assert [candidate.reddit_post_id for candidate in result.candidates] == ["one", "two"]
    assert "after=t3_one" in session.calls[1]["url"]
    assert result.completed_routes == ["public_json_top"]


def test_public_json_does_not_paginate_without_after() -> None:
    session = FakeSession([_listing_response([_post("one")])])
    result = acquire_public_json(
        _client(session),
        subreddit="ApplyingToCollege",
        candidate_limit=10,
        max_pages_per_route=3,
        routes=PUBLIC_JSON_ROUTES[:1],
    )

    assert len(session.calls) == 1
    assert len(result.candidates) == 1


@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (FakeResponse(200, "not-json"), "MalformedJson"),
        (FakeResponse(200, json.dumps({"unexpected": {}})), "UnexpectedSchema"),
        (FakeResponse(200, "<html></html>", content_type="text/html"), "UnexpectedContentType"),
    ],
)
def test_public_json_rejects_malformed_responses(
    response: FakeResponse,
    error_type: str,
) -> None:
    result = acquire_public_json(
        _client(FakeSession([response])),
        subreddit="ApplyingToCollege",
        candidate_limit=5,
        routes=PUBLIC_JSON_ROUTES[:1],
    )

    assert result.candidates == []
    assert result.errors[0].error_type == error_type


def test_deleted_and_removed_records_reach_the_existing_hard_filter() -> None:
    deleted = _post("deleted", selftext="[deleted]", author=None)
    removed = _post(
        "removed",
        selftext="[removed]",
        removed_by_category="moderator",
    )
    result = acquire_public_json(
        _client(FakeSession([_listing_response([deleted, removed])])),
        subreddit="ApplyingToCollege",
        candidate_limit=5,
        routes=PUBLIC_JSON_ROUTES[:1],
    )

    decisions = {
        candidate.reddit_post_id: evaluate_hard_filters(candidate).reason_code
        for candidate in result.candidates
    }
    assert decisions == {
        "deleted": RejectionCode.DELETED_CONTENT.value,
        "removed": RejectionCode.REMOVED_CONTENT.value,
    }


def test_public_json_403_stops_provider_without_trying_other_routes() -> None:
    session = FakeSession([FakeResponse(403, "denied", content_type="text/html")])
    result = acquire_public_json(
        _client(session),
        subreddit="ApplyingToCollege",
        candidate_limit=20,
    )

    assert len(session.calls) == 1
    assert result.errors[0].status_code == 403
    assert "without bypass" in result.errors[0].message


def test_http_429_honors_retry_after_once() -> None:
    sleeps: list[float] = []
    session = FakeSession(
        [
            FakeResponse(429, "", headers={"Retry-After": "2"}),
            _listing_response([_post("recovered")]),
        ]
    )
    result = acquire_public_json(
        _client(session, max_retries=1, sleep_fn=sleeps.append),
        subreddit="ApplyingToCollege",
        candidate_limit=5,
        routes=PUBLIC_JSON_ROUTES[:1],
    )

    assert sleeps == [2.0]
    assert len(session.calls) == 2
    assert [candidate.reddit_post_id for candidate in result.candidates] == ["recovered"]


def test_http_timeout_and_5xx_have_bounded_retries() -> None:
    timeout_session = FakeSession(
        [requests.Timeout("slow"), _listing_response([_post("after-timeout")])]
    )
    timeout_result = acquire_public_json(
        _client(timeout_session, max_retries=1),
        subreddit="ApplyingToCollege",
        candidate_limit=5,
        routes=PUBLIC_JSON_ROUTES[:1],
    )
    server_session = FakeSession([FakeResponse(503, ""), FakeResponse(503, "")])
    server_result = acquire_public_json(
        _client(server_session, max_retries=1),
        subreddit="ApplyingToCollege",
        candidate_limit=5,
        routes=PUBLIC_JSON_ROUTES[:1],
    )

    assert len(timeout_session.calls) == 2
    assert timeout_result.candidates[0].reddit_post_id == "after-timeout"
    assert len(server_session.calls) == 2
    assert server_result.errors[0].status_code == 503


def test_request_cap_stops_pagination() -> None:
    session = FakeSession([_listing_response([_post("one")], after="t3_one")])
    client = _client(session, request_limit=1)
    result = acquire_public_json(
        client,
        subreddit="ApplyingToCollege",
        candidate_limit=5,
        max_pages_per_route=2,
        routes=PUBLIC_JSON_ROUTES[:1],
    )

    assert len(session.calls) == 1
    assert result.errors[0].error_type == RequestLimitReached.__name__


def test_successful_response_cache_is_reused(tmp_path: Path) -> None:
    session = FakeSession([_listing_response([_post("cached")])])
    client = _client(session, cache_dir=tmp_path)
    first = acquire_public_json(
        client,
        subreddit="ApplyingToCollege",
        candidate_limit=5,
        routes=PUBLIC_JSON_ROUTES[:1],
    )
    second = acquire_public_json(
        client,
        subreddit="ApplyingToCollege",
        candidate_limit=5,
        routes=PUBLIC_JSON_ROUTES[:1],
    )

    assert first.candidates[0].reddit_post_id == second.candidates[0].reddit_post_id
    assert len(session.calls) == 1
    assert client.metrics.cache_hit_count == 1
    assert client.metrics.cache_miss_count == 1


def test_expired_and_corrupt_cache_entries_are_pruned(tmp_path: Path) -> None:
    expired = tmp_path / "expired.json"
    corrupt = tmp_path / "corrupt.json"
    expired.write_text(
        json.dumps({"fetched_at_epoch": 1, "url": "https://example.com"}),
        encoding="utf-8",
    )
    corrupt.write_text("not-json", encoding="utf-8")

    _client(FakeSession([]), cache_dir=tmp_path)

    assert not expired.exists()
    assert not corrupt.exists()


def _client(
    session: FakeSession,
    *,
    request_limit: int = 10,
    max_retries: int = 0,
    sleep_fn: Any = lambda _: None,
    cache_dir: Path | None = None,
) -> BoundedHttpClient:
    return BoundedHttpClient(
        request_limit=request_limit,
        cache_dir=cache_dir,
        session=session,
        max_retries=max_retries,
        sleep_fn=sleep_fn,
    )


def _listing_response(
    records: list[dict[str, Any]],
    *,
    after: str | None = None,
) -> FakeResponse:
    return FakeResponse(
        200,
        json.dumps(
            {
                "data": {
                    "after": after,
                    "children": [{"kind": "t3", "data": record} for record in records],
                }
            }
        ),
    )


def _post(post_id: str, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": post_id,
        "name": f"t3_{post_id}",
        "subreddit": "ApplyingToCollege",
        "title": "Detailed Common App essay guide",
        "selftext": "Actionable college application guidance with examples. " * 10,
        "permalink": f"/r/ApplyingToCollege/comments/{post_id}/guide/",
        "url": f"https://www.reddit.com/r/ApplyingToCollege/comments/{post_id}/guide/",
        "author": "helpful_user",
        "created_utc": 1_710_000_000,
        "score": 123,
        "upvote_ratio": 0.95,
        "num_comments": 30,
        "link_flair_text": "Best of A2C",
        "is_self": True,
        "removed_by_category": None,
    }
    values.update(overrides)
    return values
