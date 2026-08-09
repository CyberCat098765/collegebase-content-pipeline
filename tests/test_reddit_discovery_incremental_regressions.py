from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.reddit_discovery import pipeline
from src.reddit_discovery.discovery import (
    candidate_from_submission,
    merge_candidates_by_id_and_url,
)
from src.reddit_discovery.filtering import evaluate_hard_filters
from src.reddit_discovery.models import AuthProbeResult
from src.reddit_discovery.pipeline import RedditDiscoveryOptions, run_reddit_discovery


class OAuthException(Exception):
    pass


class CommentFetchError(Exception):
    pass


@pytest.fixture
def reddit_factory(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(current=None)

    def create_client(*, load_env: bool) -> _Reddit:
        assert load_env is True
        assert state.current is not None
        return state.current

    monkeypatch.setattr(pipeline, "create_reddit_client", create_client)
    monkeypatch.setattr(
        pipeline,
        "probe_reddit_auth",
        lambda reddit, subreddit: AuthProbeResult(
            success=True,
            read_only=True,
            subreddit=subreddit,
            subreddit_title="Applying to College",
            accessible=True,
            rate_limit=dict(reddit.auth.limits),
        ),
    )
    return state


def test_unchanged_duplicate_is_promoted_when_representative_is_removed(
    tmp_path: Path,
    reddit_factory: SimpleNamespace,
) -> None:
    output_dir = tmp_path / "outputs"
    shared_url = "https://example.edu/application-guide"
    reddit_factory.current = _Reddit(
        [
            _submission("strong", shared_url, distinguished="moderator", stickied=True),
            _submission("backup", shared_url),
        ]
    )
    first = run_reddit_discovery(_options(output_dir))
    assert [item["reddit_post_id"] for item in first.bundle.accepted_resources] == ["strong"]
    assert first.summary["duplicate_cluster_count"] == 1

    removed = _submission("strong", shared_url, distinguished="moderator", stickied=True)
    removed.selftext = "[removed]"
    removed.removed_by_category = "moderator"
    reddit_factory.current = _Reddit([removed, _submission("backup", shared_url)])
    second = run_reddit_discovery(_options(output_dir))

    assert [item["reddit_post_id"] for item in second.bundle.accepted_resources] == ["backup"]
    rejected = _jsonl(output_dir / "rejected_candidates.jsonl")
    assert [(item["reddit_post_id"], item["rejection_reason"]) for item in rejected] == [
        ("strong", "REMOVED_CONTENT")
    ]


def test_unchanged_duplicate_cluster_remains_auditable(
    tmp_path: Path,
    reddit_factory: SimpleNamespace,
) -> None:
    output_dir = tmp_path / "outputs"
    shared_url = "https://example.edu/application-guide"
    submissions = [
        _submission("strong", shared_url, distinguished="moderator", stickied=True),
        _submission("backup", shared_url),
    ]
    reddit_factory.current = _Reddit(submissions)
    run_reddit_discovery(_options(output_dir))

    reddit_factory.current = _Reddit(
        [
            _submission("strong", shared_url, distinguished="moderator", stickied=True),
            _submission("backup", shared_url),
        ]
    )
    second = run_reddit_discovery(_options(output_dir))

    assert second.summary["duplicate_cluster_count"] == 1
    assert second.summary["duplicate_candidate_count"] == 1
    assert second.bundle.duplicate_clusters[0]["retained_reddit_post_id"] == "strong"


def test_resume_retries_only_incomplete_route_after_failure(
    tmp_path: Path,
    reddit_factory: SimpleNamespace,
) -> None:
    output_dir = tmp_path / "outputs"
    failing_subreddit = _Subreddit([_submission("guide", "https://example.edu/guide")])
    failing_subreddit.hot_error = RuntimeError("listing failed")
    reddit_factory.current = _Reddit([], subreddit=failing_subreddit)
    first = run_reddit_discovery(_options(output_dir))

    assert first.summary["completed_route_count"] == 10
    assert first.summary["status"] == "completed_with_errors"

    resumed_subreddit = _Subreddit([])
    reddit_factory.current = _Reddit([], subreddit=resumed_subreddit)
    resumed = run_reddit_discovery(_options(output_dir, resume=True))

    assert resumed.summary["completed_route_count"] == 11
    assert [call[0] for call in resumed_subreddit.calls] == ["hot"]
    assert resumed.summary["route_counts"]["top:year"] == 1


def test_failed_comment_collection_is_retried_on_next_run(
    tmp_path: Path,
    reddit_factory: SimpleNamespace,
) -> None:
    output_dir = tmp_path / "outputs"
    failing = _submission("guide", "https://example.edu/guide")
    failing.comments = _FailingComments()
    first_reddit = _Reddit([failing])
    reddit_factory.current = first_reddit
    first = run_reddit_discovery(_options(output_dir, include_comments=True))

    assert first.summary["accepted_count"] == 1
    assert first.summary["error_count"] == 1
    registry = json.loads((output_dir / "source_registry.json").read_text())
    assert registry["posts"]["guide"]["processing_status"] == "failed"
    assert registry["posts"]["guide"]["failure_reason"] == "COMMENT_FETCH_FAILED"
    assert first_reddit.comment_requests == ["guide"]

    working_reddit = _Reddit([_submission("guide", "https://example.edu/guide")])
    reddit_factory.current = working_reddit
    second = run_reddit_discovery(_options(output_dir, include_comments=True))

    assert second.summary["skipped_unchanged_count"] == 0
    assert working_reddit.comment_requests == ["guide"]
    assert len(second.bundle.accepted_resources[0]["comments"]) == 1


def test_global_discovery_abort_skips_comment_api_calls(
    tmp_path: Path,
    reddit_factory: SimpleNamespace,
) -> None:
    output_dir = tmp_path / "outputs"
    submission = _submission("guide", "https://example.edu/guide")
    subreddit = _PartialAuthFailureSubreddit([submission])
    reddit = _Reddit([], subreddit=subreddit)
    reddit_factory.current = reddit

    result = run_reddit_discovery(_options(output_dir, include_comments=True))

    assert result.summary["accepted_count"] == 1
    assert result.summary["error_count"] == 2
    assert reddit.comment_requests == []
    registry = json.loads((output_dir / "source_registry.json").read_text())
    assert registry["posts"]["guide"]["processing_status"] == "failed"


def test_same_id_merge_preserves_later_removed_state() -> None:
    original = _submission("guide", "https://example.edu/guide")
    removed = _submission("guide", "https://example.edu/guide")
    removed.selftext = "[removed]"
    removed.removed_by_category = "moderator"
    removed.author = None
    first = candidate_from_submission(original, "top:year", "2026-08-01T00:00:00Z")
    second = candidate_from_submission(removed, "hot", "2026-08-01T00:01:00Z")

    merged, duplicates = merge_candidates_by_id_and_url([first, second])

    assert duplicates == []
    assert merged[0].selftext == "[removed]"
    assert merged[0].removed_by_category == "moderator"
    assert merged[0].author_name is None
    assert merged[0].retrieved_at == "2026-08-01T00:01:00Z"
    assert evaluate_hard_filters(merged[0]).reason_code == "REMOVED_CONTENT"


class _Reddit:
    def __init__(self, submissions: list[SimpleNamespace], *, subreddit: _Subreddit | None = None) -> None:
        self.read_only = True
        self.auth = SimpleNamespace(limits={"used": 1, "remaining": 599})
        self.subreddit_object = subreddit or _Subreddit(submissions)
        all_submissions = submissions or self.subreddit_object.submissions
        self._submissions = {item.id: item for item in all_submissions}
        self.comment_requests: list[str] = []

    def subreddit(self, name: str) -> _Subreddit:
        assert name == "ApplyingToCollege"
        return self.subreddit_object

    def submission(self, *, id: str) -> SimpleNamespace:
        self.comment_requests.append(id)
        return self._submissions[id]


class _Subreddit:
    def __init__(self, submissions: list[SimpleNamespace]) -> None:
        self.submissions = submissions
        self.calls: list[tuple[Any, ...]] = []
        self.hot_error: Exception | None = None

    def top(self, **kwargs: Any) -> list[SimpleNamespace]:
        self.calls.append(("top", kwargs))
        return list(self.submissions)

    def hot(self, **kwargs: Any) -> list[SimpleNamespace]:
        self.calls.append(("hot", kwargs))
        if self.hot_error is not None:
            raise self.hot_error
        return []

    def new(self, **kwargs: Any) -> list[SimpleNamespace]:
        self.calls.append(("new", kwargs))
        return []

    def search(self, query: str, **kwargs: Any) -> list[SimpleNamespace]:
        self.calls.append(("search", query, kwargs))
        return []


class _PartialAuthFailureSubreddit(_Subreddit):
    def top(self, **kwargs: Any):
        self.calls.append(("top", kwargs))
        yield from self.submissions
        raise OAuthException("credentials revoked during listing")


class _Comments(list[Any]):
    def __init__(self) -> None:
        super().__init__(
            [
                SimpleNamespace(
                    id="useful",
                    author=SimpleNamespace(name="commenter"),
                    body="This detailed comment adds concrete steps applicants can verify before using the advice.",
                    score=10,
                    created_utc=1_710_000_100,
                    permalink="/r/ApplyingToCollege/comments/guide/useful",
                )
            ]
        )

    def replace_more(self, *, limit: int) -> None:
        assert limit == 0


class _FailingComments(list[Any]):
    def replace_more(self, *, limit: int) -> None:
        raise CommentFetchError("comment endpoint failed")


def _submission(
    post_id: str,
    url: str,
    *,
    distinguished: str | None = None,
    stickied: bool = False,
) -> SimpleNamespace:
    paragraph = (
        "This college application essay guide explains each practical step with a concrete "
        "example, checklist, timeline, template, warning, and choice for future applicants. "
        "According to official requirements, policies may vary, so check current guidance. "
    )
    body = "Planning Guide:\n" + paragraph * 16
    body += "\n1. Collect examples.\n2. Compare options.\n3. Verify every deadline."
    return SimpleNamespace(
        id=post_id,
        name=f"t3_{post_id}",
        subreddit=SimpleNamespace(display_name="ApplyingToCollege"),
        title="Comprehensive College Application Essay Guide",
        selftext=body,
        permalink=f"/r/ApplyingToCollege/comments/{post_id}/guide",
        url=url,
        author=SimpleNamespace(name="helpful_user"),
        created_utc=1_710_000_000,
        score=900,
        upvote_ratio=0.97,
        num_comments=100,
        link_flair_text="Advice",
        is_self=False,
        is_original_content=True,
        over_18=False,
        spoiler=False,
        stickied=stickied,
        distinguished=distinguished,
        locked=False,
        archived=False,
        removed_by_category=None,
        comments=_Comments(),
    )


def _options(output_dir: Path, **overrides: Any) -> RedditDiscoveryOptions:
    values: dict[str, Any] = {
        "quick": True,
        "provider": "praw",
        "no_llm": True,
        "output_dir": output_dir,
    }
    values.update(overrides)
    return RedditDiscoveryOptions(**values)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]
