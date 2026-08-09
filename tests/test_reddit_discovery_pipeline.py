from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.reddit_discovery import pipeline
from src.reddit_discovery.models import AuthProbeResult
from src.reddit_discovery.outputs import (
    ACCEPTED_RESOURCE_FIELDS,
    OUTPUT_FILENAMES,
    validate_output_bundle,
)
from src.reddit_discovery.pipeline import RedditDiscoveryOptions, run_reddit_discovery


@pytest.fixture
def reddit_factory(monkeypatch: pytest.MonkeyPatch) -> _RedditFactory:
    factory = _RedditFactory()
    monkeypatch.setattr(pipeline, "create_reddit_client", factory)
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
    return factory


def test_quick_heuristic_run_writes_outputs_and_filters_before_comments(
    tmp_path: Path,
    reddit_factory: _RedditFactory,
) -> None:
    reddit = _FakeReddit(
        [
            _essay_submission("essay"),
            _review_submission("review"),
            _blocked_submission("blocked"),
        ]
    )
    reddit_factory.current = reddit
    output_dir = tmp_path / "outputs"

    result = run_reddit_discovery(
        _options(output_dir, include_comments=True)
    )

    assert result.bundle is not None
    assert result.summary["candidate_count"] == 3
    assert result.summary["accepted_count"] == 1
    assert result.summary["human_review_count"] == 1
    assert result.summary["rejected_count"] == 1
    assert result.summary["mode"] == "quick"
    assert result.summary["llm_status"] == "disabled_by_flag"
    assert validate_output_bundle(result.bundle) == []

    expected_names = set(OUTPUT_FILENAMES.values())
    assert len(result.written_paths) == 8
    assert {path.name for path in result.written_paths} == expected_names
    assert all((output_dir / name).is_file() for name in expected_names)

    accepted = _load_json(output_dir / "accepted_resources.json")
    review = _load_json(output_dir / "human_review.json")
    rejected = _load_jsonl(output_dir / "rejected_candidates.jsonl")
    assert len(accepted) == len(review) == len(rejected) == 1
    assert set(ACCEPTED_RESOURCE_FIELDS) == set(accepted[0])
    assert accepted[0]["reddit_post_id"] == "essay"
    assert review[0]["reddit_post_id"] == "review"
    assert rejected[0]["reddit_post_id"] == "blocked"
    assert rejected[0]["rejection_reason"] == "BLOCKED_CATEGORY_CHANCE_ME"

    assert reddit.comment_requests == ["essay", "review"]
    assert "blocked" not in reddit.comment_requests
    assert len(reddit.subreddit_object.calls) == 11


def test_second_unchanged_run_skips_processing(
    tmp_path: Path,
    reddit_factory: _RedditFactory,
) -> None:
    output_dir = tmp_path / "outputs"
    reddit_factory.current = _FakeReddit(
        [_essay_submission("essay"), _review_submission("review"), _blocked_submission("blocked")]
    )
    run_reddit_discovery(_options(output_dir, include_comments=True))

    second_reddit = _FakeReddit(
        [_essay_submission("essay"), _review_submission("review"), _blocked_submission("blocked")]
    )
    reddit_factory.current = second_reddit
    second = run_reddit_discovery(_options(output_dir, include_comments=True))

    assert second.summary["skipped_unchanged_count"] == 3
    assert second.summary["reprocessed_changed_count"] == 0
    assert second_reddit.comment_requests == []
    assert len(second_reddit.subreddit_object.calls) == 11
    rejected = _load_jsonl(output_dir / "rejected_candidates.jsonl")
    assert [item["reddit_post_id"] for item in rejected] == ["blocked"]


def test_changed_content_and_force_reprocess_candidates(
    tmp_path: Path,
    reddit_factory: _RedditFactory,
) -> None:
    output_dir = tmp_path / "outputs"
    reddit_factory.current = _FakeReddit([_essay_submission("essay")])
    first = run_reddit_discovery(_options(output_dir, include_comments=True))
    first_hash = first.bundle.accepted_resources[0]["content_hash"]  # type: ignore[union-attr]

    changed_reddit = _FakeReddit(
        [_essay_submission("essay", suffix=" A changed section explains revision feedback.")]
    )
    reddit_factory.current = changed_reddit
    changed = run_reddit_discovery(_options(output_dir, include_comments=True))

    assert changed.summary["reprocessed_changed_count"] == 1
    assert changed.summary["skipped_unchanged_count"] == 0
    assert changed.summary["rejected_count"] == 0
    assert changed.summary["duplicate_cluster_count"] == 0
    assert changed_reddit.comment_requests == ["essay"]
    changed_hash = changed.bundle.accepted_resources[0]["content_hash"]  # type: ignore[union-attr]
    assert changed_hash != first_hash

    forced_reddit = _FakeReddit(
        [_essay_submission("essay", suffix=" A changed section explains revision feedback.")]
    )
    reddit_factory.current = forced_reddit
    forced = run_reddit_discovery(
        _options(output_dir, include_comments=True, force=True)
    )

    assert forced.summary["skipped_unchanged_count"] == 0
    assert forced.summary["accepted_count"] == 1
    assert forced.summary["rejected_count"] == 0
    assert forced_reddit.comment_requests == ["essay"]


def test_resume_after_completed_run_starts_fresh_discovery(
    tmp_path: Path,
    reddit_factory: _RedditFactory,
) -> None:
    output_dir = tmp_path / "outputs"
    reddit_factory.current = _FakeReddit([_essay_submission("essay")])
    first = run_reddit_discovery(_options(output_dir))
    assert first.summary["completed_route_count"] == 11

    resumed_reddit = _FakeReddit([])
    reddit_factory.current = resumed_reddit
    resumed = run_reddit_discovery(_options(output_dir, resume=True))

    assert resumed.summary["completed_route_count"] == 11
    assert resumed.summary["candidate_count"] == 1
    assert resumed.summary["skipped_unchanged_count"] == 0
    assert len(resumed_reddit.subreddit_object.calls) == 11


def test_resume_uses_interrupted_checkpoint_without_repeating_routes(
    tmp_path: Path,
    reddit_factory: _RedditFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "outputs"
    reddit_factory.current = _FakeReddit([_essay_submission("essay")])
    original_writer = pipeline.write_output_bundle

    def fail_write(*args: Any, **kwargs: Any) -> tuple[Path, ...]:
        raise OSError("simulated interrupted output write")

    monkeypatch.setattr(pipeline, "write_output_bundle", fail_write)
    with pytest.raises(OSError, match="simulated interrupted"):
        run_reddit_discovery(_options(output_dir))

    monkeypatch.setattr(pipeline, "write_output_bundle", original_writer)
    resumed_reddit = _FakeReddit([])
    reddit_factory.current = resumed_reddit
    resumed = run_reddit_discovery(_options(output_dir, resume=True))

    assert resumed.summary["completed_route_count"] == 11
    assert resumed.summary["candidate_count"] == 1
    assert resumed.summary["accepted_count"] == 1
    assert resumed_reddit.subreddit_object.calls == []
    assert resumed.summary["route_counts"]["top:year"] == 1


def test_accepted_limit_moves_lower_ranked_resource_to_review(
    tmp_path: Path,
    reddit_factory: _RedditFactory,
) -> None:
    output_dir = tmp_path / "outputs"
    reddit_factory.current = _FakeReddit(
        [
            _essay_submission("essay"),
            _review_submission(
                "aid",
                distinguished="moderator",
                stickied=True,
                url="https://studentaid.gov/complete-aid-process",
            ),
        ]
    )

    result = run_reddit_discovery(_options(output_dir, accepted_limit=1))

    assert result.summary["accepted_count"] == 1
    assert result.summary["human_review_count"] == 1
    assert result.summary["rejected_count"] == 0
    review = _load_json(output_dir / "human_review.json")
    assert review[0]["reddit_post_id"] == "aid"
    assert review[0]["rejection_reason"] == "ACCEPTED_LIMIT_REACHED"

    reddit_factory.current = _FakeReddit(
        [
            _essay_submission("essay"),
            _review_submission(
                "aid",
                distinguished="moderator",
                stickied=True,
                url="https://studentaid.gov/complete-aid-process",
            ),
        ]
    )
    unlimited = run_reddit_discovery(_options(output_dir))

    assert unlimited.summary["accepted_count"] == 2
    assert unlimited.summary["human_review_count"] == 0
    assert unlimited.summary["rejected_count"] == 0


def test_output_history_preserves_previous_bundle(
    tmp_path: Path,
    reddit_factory: _RedditFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter(
        (
            "2026-08-01T00:00:00Z",
            "2026-08-01T00:00:01Z",
            "2026-08-01T00:00:02Z",
            "2026-08-01T00:00:03Z",
        )
    )
    monkeypatch.setattr(pipeline, "utc_now", lambda: next(clock))
    output_dir = tmp_path / "outputs"
    reddit_factory.current = _FakeReddit([_essay_submission("essay")])
    run_reddit_discovery(_options(output_dir))
    first_bundle = {
        filename: (output_dir / filename).read_bytes()
        for filename in OUTPUT_FILENAMES.values()
    }

    reddit_factory.current = _FakeReddit(
        [_essay_submission("essay", suffix=" New material changes the content hash.")]
    )
    run_reddit_discovery(_options(output_dir))

    archive = output_dir / "history" / "2026-08-01T00-00-02Z"
    assert archive.is_dir()
    assert {
        filename: (archive / filename).read_bytes()
        for filename in OUTPUT_FILENAMES.values()
    } == first_bundle
    assert (output_dir / "accepted_resources.json").read_bytes() != first_bundle[
        "accepted_resources.json"
    ]


class _RedditFactory:
    def __init__(self) -> None:
        self.current: _FakeReddit | None = None

    def __call__(self, *, load_env: bool) -> _FakeReddit:
        assert load_env is True
        assert self.current is not None
        return self.current


class _FakeReddit:
    def __init__(self, submissions: list[SimpleNamespace]) -> None:
        self.read_only = True
        self.auth = SimpleNamespace(limits={"used": 1, "remaining": 599, "reset_timestamp": 0})
        self.subreddit_object = _FakeSubreddit(submissions)
        self._submissions = {submission.id: submission for submission in submissions}
        self.comment_requests: list[str] = []

    def subreddit(self, name: str) -> _FakeSubreddit:
        assert name == "ApplyingToCollege"
        return self.subreddit_object

    def submission(self, *, id: str) -> SimpleNamespace:
        self.comment_requests.append(id)
        return self._submissions[id]


class _FakeSubreddit:
    def __init__(self, submissions: list[SimpleNamespace]) -> None:
        self.submissions = submissions
        self.calls: list[tuple[Any, ...]] = []

    def top(self, **kwargs: Any) -> list[SimpleNamespace]:
        self.calls.append(("top", kwargs))
        return list(self.submissions)

    def hot(self, **kwargs: Any) -> list[SimpleNamespace]:
        self.calls.append(("hot", kwargs))
        return []

    def new(self, **kwargs: Any) -> list[SimpleNamespace]:
        self.calls.append(("new", kwargs))
        return []

    def search(self, query: str, **kwargs: Any) -> list[SimpleNamespace]:
        self.calls.append(("search", query, kwargs))
        return []


class _FakeComments(list[Any]):
    def __init__(self) -> None:
        super().__init__(
            [
                SimpleNamespace(
                    id="useful-comment",
                    author=SimpleNamespace(name="helpful_commenter"),
                    body=(
                        "This comment adds a careful, reusable explanation with concrete "
                        "steps applicants can verify before relying on the advice."
                    ),
                    score=12,
                    created_utc=1_710_000_000,
                    permalink="/r/ApplyingToCollege/comments/post/useful-comment",
                )
            ]
        )
        self.replace_more_limit: int | None = None

    def replace_more(self, *, limit: int) -> None:
        self.replace_more_limit = limit


def _essay_submission(post_id: str, *, suffix: str = "") -> SimpleNamespace:
    paragraph = (
        "This college essay guide explains how a Common App essay and personal statement "
        "can show reflection in a college application. First choose a focused story, next "
        "write a concrete example, then compare each option and avoid a common mistake. "
        "Use a checklist, timeline, template, and warning before the deadline. According "
        "to the official source, requirements may vary, so check current guidance. "
    )
    body = "Planning:\n" + paragraph * 12
    body += (
        "\n1. Start with a specific moment and explain why it matters to the applicant."
        "\n2. Review every example and remove details that repeat the activities list."
        "\n3. Ask a trusted reader to check clarity without rewriting the student's voice."
        + suffix
    )
    return _submission(
        post_id,
        title="Comprehensive Common App College Essay Guide",
        selftext=body,
        distinguished="moderator",
        stickied=True,
        score=1_000,
        num_comments=150,
        url="https://www.commonapp.org/apply/essay-prompts",
    )


def _review_submission(post_id: str, **overrides: Any) -> SimpleNamespace:
    body = (
        "Financial aid for a college application takes patient planning. Start with a "
        "checklist for the FAFSA, CSS Profile, and each college's separate requirements. "
        "Families should compare net price estimates instead of assuming the published "
        "tuition is the final cost. Avoid waiting until an admissions deadline to find "
        "missing tax records or account details.\n"
        "A simple timeline can separate tasks that belong to the student from questions "
        "that require a parent or guardian. Record the status of every form and review "
        "need-based aid instructions for each college before submitting.\n"
        "1. Gather household financial documents and confirm whose information is required.\n"
        "2. Compare each form entry with the source document before submitting it.\n"
        "3. Save confirmation details and note when the college receives each form."
    )
    return _submission(
        post_id,
        title="Financial Aid Planning Notes",
        selftext=body,
        score=100,
        num_comments=30,
        **overrides,
    )


def _blocked_submission(post_id: str) -> SimpleNamespace:
    return _submission(
        post_id,
        title="Chance Me for My Dream College",
        selftext=(
            "My exact grades and test scores are the only context for this personal chance "
            "request, and I am asking strangers to predict one admissions result. "
        )
        * 8,
    )


def _submission(post_id: str, **overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": post_id,
        "name": f"t3_{post_id}",
        "subreddit": SimpleNamespace(display_name="ApplyingToCollege"),
        "title": "Detailed admissions resource",
        "selftext": "Reusable admissions guidance. " * 20,
        "permalink": f"/r/ApplyingToCollege/comments/{post_id}/resource",
        "url": f"https://www.reddit.com/r/ApplyingToCollege/comments/{post_id}/resource",
        "author": SimpleNamespace(name="helpful_user"),
        "created_utc": 1_710_000_000,
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
        "comments": _FakeComments(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _options(output_dir: Path, **overrides: Any) -> RedditDiscoveryOptions:
    values: dict[str, Any] = {
        "quick": True,
        "provider": "praw",
        "no_llm": True,
        "output_dir": output_dir,
        "minimum_usefulness_score": 70,
    }
    values.update(overrides)
    return RedditDiscoveryOptions(**values)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
