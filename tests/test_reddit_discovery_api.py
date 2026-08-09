from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any
import pytest

from src.reddit_discovery.auth import (
    MissingRedditCredentials,
    RedditAuthError,
    RedditCredentials,
    create_reddit_client,
    credentials_from_environment,
    probe_reddit_auth,
)
from src.reddit_discovery.constants import (
    MAX_RANGE_ROUTES,
    QUICK_ROUTES,
    SEARCH_QUERIES,
    DiscoveryRoute,
    build_discovery_routes,
)
from src.reddit_discovery.discovery import (
    candidate_from_submission,
    collect_submission_comments,
    discover_candidates,
    merge_candidates_by_id_and_url,
)
from src.reddit_discovery.models import RedditCandidate


EXPECTED_SEARCH_QUERIES = {
    "general_guides": [
        "guide", "resource", "megathread", "FAQ", "advice", "application tips",
        "things I wish I knew", "admissions guide",
    ],
    "college_list": [
        "college list", "balanced college list", "reach target safety",
        "safety schools", "choosing colleges",
    ],
    "common_app": [
        "Common App", "application timeline", "application checklist",
        "application process",
    ],
    "personal_essay": [
        "personal statement", "Common App essay", "college essay guide",
        "essay advice", "essay mistakes",
    ],
    "supplemental_essays": [
        "supplemental essays", "supplement guide", "why us essay",
        "community essay", "diversity essay",
    ],
    "activities": [
        "activities list", "extracurricular description", "Common App activities",
        "honors section", "additional information section",
    ],
    "recommendations": [
        "letter of recommendation", "recommendation letter", "teacher recommendation",
        "counselor recommendation", "brag sheet",
    ],
    "financial_aid": [
        "financial aid guide", "FAFSA", "CSS Profile", "net price calculator",
        "financial aid appeal", "need based aid", "merit aid",
    ],
    "scholarships": [
        "scholarship guide", "scholarships", "outside scholarships", "full ride",
        "merit scholarship",
    ],
    "application_rounds": [
        "early decision", "early action", "restrictive early action",
        "regular decision", "ED EA RD",
    ],
    "admissions_decisions": [
        "admissions decision guide", "understanding admissions decisions",
        "decision letter explained",
    ],
    "deferral_waitlist": [
        "deferred", "deferral guide", "waitlist guide",
        "letter of continued interest", "LOCI",
    ],
    "interviews": [
        "college interview", "alumni interview", "interview guide",
        "interview questions",
    ],
    "demonstrated_interest": [
        "demonstrated interest", "college visit", "admissions email",
    ],
    "choosing_a_college": [
        "compare college offers", "choosing between colleges", "cost versus fit",
        "enrollment decision",
    ],
    "application_logistics": [
        "application portal", "transcript submission", "application fee waiver",
        "midyear report",
    ],
    "testing": [
        "test optional", "SAT ACT admissions", "submit test scores",
        "standardized testing",
    ],
    "first_generation": [
        "first generation", "first gen applicant", "low income applicant", "QuestBridge",
    ],
    "international": [
        "international applicant guide", "international financial aid",
        "international admissions",
    ],
    "transfer": [
        "transfer application", "transfer admissions guide", "community college transfer",
    ],
    "institutional_knowledge": [
        "admissions officer AMA", "AO AMA", "verified admissions officer",
        "moderator guide",
    ],
}


def test_exact_max_range_route_signatures() -> None:
    assert SEARCH_QUERIES == EXPECTED_SEARCH_QUERIES
    expected = [
        ("listing", "top:all", 1000, "top", "all", "", "", "", "lucene"),
        ("listing", "top:year", 1000, "top", "year", "", "", "", "lucene"),
        ("listing", "top:month", 500, "top", "month", "", "", "", "lucene"),
        ("listing", "top:week", 250, "top", "week", "", "", "", "lucene"),
        ("listing", "new", 1000, "new", None, "", "", "", "lucene"),
        ("listing", "hot", 250, "hot", None, "", "", "", "lucene"),
        ("listing", "rising", 100, "rising", None, "", "", "", "lucene"),
    ]
    for group, queries in EXPECTED_SEARCH_QUERIES.items():
        for query in queries:
            for sort, time_filter in (("top", "all"), ("new", "year")):
                expected.append(
                    (
                        "search", f"search:{group}:{query}:{sort}:{time_filter}", 250,
                        "", time_filter, group, query, sort, "lucene",
                    )
                )
    assert len(MAX_RANGE_ROUTES) == 197
    assert [_route_signature(route) for route in MAX_RANGE_ROUTES] == expected


def test_exact_quick_route_signatures() -> None:
    expected = [
        ("listing", "top:year", 150, "top", "year", "", "", "", "lucene"),
        ("listing", "hot", 75, "hot", None, "", "", "", "lucene"),
        ("listing", "new", 100, "new", None, "", "", "", "lucene"),
    ]
    quick_queries = (
        ("general_guides", "guide"),
        ("personal_essay", "personal statement"),
        ("financial_aid", "financial aid guide"),
        ("common_app", "application timeline"),
    )
    for group, query in quick_queries:
        for sort, time_filter in (("top", "all"), ("new", "year")):
            expected.append(
                (
                    "search", f"search:{group}:{query}:{sort}:{time_filter}", 250,
                    "", time_filter, group, query, sort, "lucene",
                )
            )
    assert len(QUICK_ROUTES) == 11
    assert [_route_signature(route) for route in QUICK_ROUTES] == expected


def test_modes_are_mutually_exclusive_and_scope_is_a2c_only() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        build_discovery_routes(max_range=True, quick=True)
    with pytest.raises(ValueError, match="Only r/ApplyingToCollege"):
        discover_candidates(_RecordingReddit(), subreddit_name="college", routes=())


def test_missing_and_placeholder_credentials_fail_safely() -> None:
    with pytest.raises(MissingRedditCredentials, match="Missing Reddit API credentials"):
        credentials_from_environment(load_env=False, environ={})

    placeholder_secret = "replace_with_real_secret"
    with pytest.raises(MissingRedditCredentials) as captured:
        credentials_from_environment(
            load_env=False,
            environ={
                "REDDIT_CLIENT_ID": "client-id",
                "REDDIT_CLIENT_SECRET": placeholder_secret,
                "REDDIT_USER_AGENT": "windows:collegebase-reddit-discovery:v1.0.0",
            },
        )
    assert placeholder_secret not in str(captured.value)


def test_client_is_read_only_and_never_exposes_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeClient:
        read_only = False

    def reddit_constructor(**kwargs: Any) -> FakeClient:
        calls.append(kwargs)
        return FakeClient()

    monkeypatch.setitem(sys.modules, "praw", SimpleNamespace(Reddit=reddit_constructor))
    credentials = RedditCredentials(
        client_id="client-id",
        client_secret="super-secret-value",
        user_agent="windows:collegebase-reddit-discovery:v1.0.0 (by u/tester)",
    )
    client = create_reddit_client(credentials)

    assert client.read_only is True
    assert calls == [
        {
            "client_id": "client-id",
            "client_secret": "super-secret-value",
            "user_agent": "windows:collegebase-reddit-discovery:v1.0.0 (by u/tester)",
            "check_for_async": False,
        }
    ]
    assert "super-secret-value" not in repr(credentials)

    def failing_constructor(**kwargs: Any) -> None:
        raise RuntimeError(kwargs["client_secret"])

    monkeypatch.setitem(sys.modules, "praw", SimpleNamespace(Reddit=failing_constructor))
    with pytest.raises(RedditAuthError) as captured:
        create_reddit_client(credentials)
    assert "super-secret-value" not in str(captured.value)


def test_auth_probe_fetches_metadata_and_rate_limits() -> None:
    reddit = SimpleNamespace(
        read_only=True,
        auth=SimpleNamespace(limits={"used": 2, "remaining": 598, "reset_timestamp": 42}),
        subreddit=lambda name: SimpleNamespace(
            display_name=name,
            title="College Admissions",
        ),
    )
    result = probe_reddit_auth(reddit)

    assert result.success is True
    assert result.read_only is True
    assert result.subreddit == "ApplyingToCollege"
    assert result.subreddit_title == "College Admissions"
    assert result.accessible is True
    assert result.rate_limit == {"used": 2, "remaining": 598, "reset_timestamp": 42}


def test_candidate_maps_required_fields_and_round_trips_deleted_author() -> None:
    candidate = candidate_from_submission(
        _submission("post1", author=None),
        "top:all",
        "2026-08-01T00:00:00Z",
    )
    data = candidate.to_dict()
    required = {
        "reddit_post_id", "fullname", "subreddit", "title", "selftext",
        "canonical_url", "permalink", "author_name", "created_utc", "retrieved_at",
        "score", "upvote_ratio", "num_comments", "link_flair_text", "is_self",
        "is_original_content", "over_18", "spoiler", "stickied", "distinguished",
        "locked", "archived", "removed_by_category", "discovered_by",
    }
    assert required <= data.keys()
    assert data["author_name"] is None
    assert RedditCandidate.from_dict(data).to_dict() == data


def test_duplicate_ids_merge_all_discovery_origins() -> None:
    first = candidate_from_submission(_submission("same"), "top:all")
    second = candidate_from_submission(_submission("same", score=500), "hot")
    candidates, duplicates = merge_candidates_by_id_and_url([first, second])

    assert len(candidates) == 1
    assert duplicates == []
    assert candidates[0].discovered_by == ["top:all", "hot"]
    assert candidates[0].score == 500


def test_duplicate_canonical_urls_are_removed_and_preserved() -> None:
    path = "/r/ApplyingToCollege/comments/shared/resource"
    first = candidate_from_submission(
        _submission("one", permalink=path, url=f"https://www.reddit.com{path}"),
        "top:all",
    )
    second = candidate_from_submission(
        _submission(
            "two",
            permalink=f"https://old.reddit.com{path}/?utm_source=test",
            url=f"https://old.reddit.com{path}/?utm_source=test",
        ),
        "new",
    )
    candidates, duplicates = merge_candidates_by_id_and_url([first, second])

    assert len(candidates) == 1
    assert len(duplicates) == 1
    assert duplicates[0].rejection_reason == "DUPLICATE_CANONICAL_URL"
    assert duplicates[0].duplicate_of == "one"


def test_completed_routes_are_skipped_on_resume() -> None:
    reddit = _RecordingReddit()
    routes = (
        DiscoveryRoute("listing", "top:year", 5, listing="top", time_filter="year"),
        DiscoveryRoute("listing", "hot", 3, listing="hot"),
    )
    result = discover_candidates(
        reddit,
        routes=routes,
        completed_routes=["top:year"],
        sleep_fn=lambda _: None,
    )

    assert reddit.subreddit_object.calls == [("hot", {"limit": 3})]
    assert result.completed_routes == ["top:year", "hot"]


def test_candidate_limit_bounds_route_requests_and_stops_fanout() -> None:
    class PopulatedSubreddit(_RecordingSubreddit):
        def top(self, **kwargs: Any) -> list[Any]:
            self.calls.append(("top", kwargs))
            return [_submission(f"top-{index}") for index in range(kwargs["limit"])]

        def hot(self, **kwargs: Any) -> list[Any]:
            self.calls.append(("hot", kwargs))
            return [_submission(f"hot-{index}") for index in range(kwargs["limit"])]

        def new(self, **kwargs: Any) -> list[Any]:
            self.calls.append(("new", kwargs))
            return [_submission(f"new-{index}") for index in range(kwargs["limit"])]

    subreddit = PopulatedSubreddit()
    routes = (
        DiscoveryRoute("listing", "top:all", 100, listing="top", time_filter="all"),
        DiscoveryRoute("listing", "hot", 100, listing="hot"),
        DiscoveryRoute("listing", "new", 100, listing="new"),
    )

    result = discover_candidates(
        _RecordingReddit(subreddit),
        routes=routes,
        candidate_limit=2,
        sleep_fn=lambda _: None,
    )

    assert len(result.candidates) == 2
    assert result.limit_reached is True
    assert result.completed_routes == ["top:all", "hot"]
    assert subreddit.calls == [
        ("top", {"time_filter": "all", "limit": 1}),
        ("hot", {"limit": 1}),
    ]


def test_quick_mode_makes_exact_listing_and_lucene_search_calls() -> None:
    reddit = _RecordingReddit()
    result = discover_candidates(reddit, quick=True, sleep_fn=lambda _: None)
    expected: list[tuple[Any, ...]] = [
        ("top", {"time_filter": "year", "limit": 150}),
        ("hot", {"limit": 75}),
        ("new", {"limit": 100}),
    ]
    for group, query in (
        ("general_guides", "guide"),
        ("personal_essay", "personal statement"),
        ("financial_aid", "financial aid guide"),
        ("common_app", "application timeline"),
    ):
        del group
        for sort, time_filter in (("top", "all"), ("new", "year")):
            expected.append(
                (
                    "search",
                    query,
                    {
                        "sort": sort,
                        "time_filter": time_filter,
                        "syntax": "lucene",
                        "limit": 250,
                    },
                )
            )
    assert reddit.subreddit_object.calls == expected
    assert len(result.completed_routes) == 11


def test_comments_are_filtered_then_capped() -> None:
    long_valid = (
        "This explanation gives applicants a concrete sequence of steps and enough "
        "context to apply the advice responsibly."
    )
    comments = _Comments(
        [
            _comment("deleted", "[deleted]"),
            _comment("removed", "[removed]"),
            _comment("automod", long_valid, author="AutoModerator"),
            _comment("short", "A short reaction."),
            _comment("reaction", "lol" + "!" * 100),
            _comment("kept", long_valid),
            _comment("capped", long_valid + " More reusable detail."),
        ]
    )
    submission = SimpleNamespace(comments=comments)
    kept = collect_submission_comments(submission, max_comments=1)

    assert submission.comment_sort == "top"
    assert comments.replace_more_limit == 0
    assert [comment.comment_id for comment in kept] == ["kept"]


def test_temporary_errors_retry_with_bounded_exponential_backoff() -> None:
    class TooManyRequests(Exception):
        pass

    subreddit = _RecordingSubreddit()
    attempts = 0

    def flaky_hot(**kwargs: Any) -> list[Any]:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TooManyRequests("temporary")
        return []

    subreddit.hot = flaky_hot  # type: ignore[method-assign]
    reddit = _RecordingReddit(subreddit)
    sleeps: list[float] = []
    route = DiscoveryRoute("listing", "hot", 3, listing="hot")
    result = discover_candidates(
        reddit,
        routes=(route,),
        max_retries=2,
        sleep_fn=sleeps.append,
    )

    assert attempts == 3
    assert sleeps == [1.0, 2.0]
    assert result.errors == []
    assert result.completed_routes == ["hot"]


def test_permanent_errors_do_not_retry_and_are_recorded() -> None:
    class Forbidden(Exception):
        pass

    subreddit = _RecordingSubreddit()

    def forbidden_hot(**kwargs: Any) -> list[Any]:
        raise Forbidden("denied")

    subreddit.hot = forbidden_hot  # type: ignore[method-assign]
    sleeps: list[float] = []
    result = discover_candidates(
        _RecordingReddit(subreddit),
        routes=(DiscoveryRoute("listing", "hot", 3, listing="hot"),),
        max_retries=5,
        sleep_fn=sleeps.append,
    )

    assert sleeps == []
    assert result.completed_routes == []
    assert len(result.errors) == 1
    assert result.errors[0].error_type == "Forbidden"
    assert result.errors[0].retryable is False
    assert result.errors[0].attempts == 1


def _route_signature(route: DiscoveryRoute) -> tuple[Any, ...]:
    return (
        route.kind, route.origin, route.limit, route.listing, route.time_filter,
        route.query_group, route.query, route.sort, route.syntax,
    )


def _submission(post_id: str, **overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": post_id,
        "name": f"t3_{post_id}",
        "subreddit": SimpleNamespace(display_name="ApplyingToCollege"),
        "title": "Detailed application guide",
        "selftext": "A reusable step-by-step admissions explanation. " * 12,
        "permalink": f"/r/ApplyingToCollege/comments/{post_id}/guide",
        "url": f"https://www.reddit.com/r/ApplyingToCollege/comments/{post_id}/guide",
        "author": SimpleNamespace(name="helpful_user"),
        "created_utc": 1710000000,
        "score": 100,
        "upvote_ratio": 0.97,
        "num_comments": 20,
        "link_flair_text": "Advice",
        "is_self": True,
        "is_original_content": True,
        "over_18": False,
        "spoiler": False,
        "stickied": False,
        "distinguished": None,
        "locked": False,
        "archived": False,
        "removed_by_category": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _RecordingSubreddit:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def top(self, **kwargs: Any) -> list[Any]:
        self.calls.append(("top", kwargs))
        return []

    def hot(self, **kwargs: Any) -> list[Any]:
        self.calls.append(("hot", kwargs))
        return []

    def new(self, **kwargs: Any) -> list[Any]:
        self.calls.append(("new", kwargs))
        return []

    def search(self, query: str, **kwargs: Any) -> list[Any]:
        self.calls.append(("search", query, kwargs))
        return []


class _RecordingReddit:
    def __init__(self, subreddit: _RecordingSubreddit | None = None) -> None:
        self.subreddit_object = subreddit or _RecordingSubreddit()

    def subreddit(self, name: str) -> _RecordingSubreddit:
        assert name == "ApplyingToCollege"
        return self.subreddit_object


class _Comments(list[Any]):
    replace_more_limit: int | None = None

    def replace_more(self, limit: int = 0) -> None:
        self.replace_more_limit = limit


def _comment(comment_id: str, body: str, author: str = "helper") -> SimpleNamespace:
    return SimpleNamespace(
        id=comment_id,
        author=SimpleNamespace(name=author),
        body=body,
        score=5,
        created_utc=1710000000,
        permalink=f"/r/ApplyingToCollege/comments/post/{comment_id}",
    )
