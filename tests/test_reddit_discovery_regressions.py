from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.reddit_discovery.auth_retry import probe_reddit_auth_with_retries
from src.reddit_discovery.dedupe import (
    DUPLICATE_CANONICAL_URL,
    deduplicate_candidates,
)
from src.reddit_discovery.models import RedditCandidate, candidate_content_hash
from src.reddit_discovery.retry import is_retryable_reddit_error, retry_delay
from src.reddit_discovery.run_support import (
    archive_existing_outputs,
    refresh_carried_metadata,
)
from src.reddit_discovery.scoring import apply_heuristic_evaluation, classify_candidate


class TooManyRequests(Exception):
    pass


class ResponseException(Exception):
    def __init__(self, status_code: int, retry_after: str = "") -> None:
        super().__init__(f"HTTP {status_code}")
        headers = {"Retry-After": retry_after} if retry_after else {}
        self.response = SimpleNamespace(status_code=status_code, headers=headers)


class BadJSON(ResponseException):
    pass


def test_exact_url_dedupe_keeps_stronger_representative() -> None:
    weak = _candidate(
        "weak",
        "https://example.edu/admissions/guide?id=1&utm_source=reddit",
        final_score=40,
    )
    strong = _candidate(
        "strong",
        "https://example.edu/admissions/guide?id=1",
        final_score=92,
        distinguished="moderator",
        stickied=True,
    )

    result = deduplicate_candidates([weak, strong])

    assert [item.reddit_post_id for item in result.retained] == ["strong"]
    assert len(result.duplicates) == 1
    assert result.duplicates[0].candidate.reddit_post_id == "weak"
    assert result.duplicates[0].reason_code == DUPLICATE_CANONICAL_URL
    assert weak.duplicate_of == "reddit_strong"


def test_custom_threshold_cannot_accept_score_below_absolute_floor() -> None:
    candidate = _candidate("floor", "https://example.edu/floor", final_score=55)

    decision = classify_candidate(candidate, minimum_usefulness_score=40)

    assert decision.status == "rejected"
    assert decision.reason_code == "LOW_USEFULNESS_SCORE"


def test_curated_resource_below_floor_is_sent_to_human_review() -> None:
    candidate = _candidate("curated", "https://example.edu/curated", final_score=56)
    candidate.title = "Organized College Application Resources Masterpost"
    candidate.primary_topic = "general_application"
    candidate.acquisition_method = "curated_a2c"

    decision = classify_candidate(candidate)

    assert decision.status == "human_review"
    assert decision.reason_code == "CURATED_RESOURCE_REVIEW"


def test_output_history_keeps_only_two_latest_generations(tmp_path: Path) -> None:
    output = tmp_path / "outputs"
    output.mkdir()
    current = output / "accepted_resources.json"
    current.write_text("first", encoding="utf-8")
    archive_existing_outputs(output, "2026-08-01T00:00:00Z")
    current.write_text("second", encoding="utf-8")
    archive_existing_outputs(output, "2026-08-02T00:00:00Z")
    current.write_text("third", encoding="utf-8")
    archive_existing_outputs(output, "2026-08-03T00:00:00Z")

    archives = sorted(path.name for path in (output / "history").iterdir())
    assert archives == ["2026-08-02T00-00-00Z", "2026-08-03T00-00-00Z"]


def test_score_in_sixties_always_requires_human_review() -> None:
    candidate = _candidate("review-band", "https://example.edu/review", final_score=65)

    decision = classify_candidate(candidate, minimum_usefulness_score=40)

    assert decision.status == "human_review"
    assert decision.reason_code == "HUMAN_REVIEW_SCORE_BAND"


def test_zero_controlled_topic_relevance_cannot_be_accepted() -> None:
    candidate = _candidate("garden", "https://example.edu/gardening", final_score=75)
    candidate.primary_topic = "other"
    candidate.score_breakdown["topic_relevance"] = {"score": 0}

    decision = classify_candidate(candidate)

    assert decision.status == "rejected"
    assert decision.reason_code == "OFF_TOPIC_NO_ADMISSIONS_RELEVANCE"


def test_contextual_applicant_workflow_maps_to_general_application() -> None:
    candidate = _candidate(
        "workflow",
        "https://example.edu/admissions/deadlines",
        final_score=0,
    )
    candidate.title = "Deadlines and documents every applicant needs"
    candidate.selftext = (
        "Applicants should use this counselor checklist for every application submission. "
        "First collect the required forms, next verify each deadline, then compare the "
        "requirements and avoid missing a recommendation. This example explains a reusable "
        "timeline and warns applicants to confirm current official requirements. "
    ) * 18
    candidate.score = 1_000

    apply_heuristic_evaluation(candidate)
    decision = classify_candidate(candidate)

    assert candidate.primary_topic == "general_application"
    assert candidate.score_breakdown["topic_relevance"]["score"] > 0
    assert decision.status == "accepted"


def test_processing_hash_tracks_engagement_and_moderation_changes() -> None:
    candidate = _candidate("hash", "https://example.edu/hash", final_score=70)
    original = candidate_content_hash(candidate)
    score_changed = RedditCandidate.from_dict(candidate.to_dict())
    score_changed.score += 1
    stickied_changed = RedditCandidate.from_dict(candidate.to_dict())
    stickied_changed.stickied = True
    moderator_changed = RedditCandidate.from_dict(candidate.to_dict())
    moderator_changed.distinguished = "moderator"

    assert candidate_content_hash(score_changed) != original
    assert candidate_content_hash(stickied_changed) != original
    assert candidate_content_hash(moderator_changed) != original


def test_carried_metadata_clears_a_deleted_author() -> None:
    carried = _candidate("author", "https://example.edu/author", final_score=70)
    observed = RedditCandidate.from_dict(carried.to_dict())
    observed.author_name = None

    refresh_carried_metadata(carried, observed)

    assert carried.author_name is None


def test_retry_after_is_not_truncated_and_bad_json_is_retryable() -> None:
    rate_limited = ResponseException(429, retry_after="120")

    assert retry_delay(rate_limited, attempt=1, base_seconds=1) == 120
    assert is_retryable_reddit_error(BadJSON(200)) is True


def test_auth_probe_retries_transient_metadata_failures() -> None:
    subreddit = _TransientSubreddit()
    reddit = _AuthReddit(subreddit)

    result = probe_reddit_auth_with_retries(
        reddit,
        max_retries=2,
        backoff_base_seconds=0,
        sleep_fn=lambda _: None,
    )

    assert result.success is True
    assert result.accessible is True
    assert result.subreddit == "ApplyingToCollege"
    assert subreddit.attempts == 3


def test_output_archives_do_not_collide_within_same_second(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    current = output_dir / "run_summary.json"
    current.write_text("first", encoding="utf-8")

    archive_existing_outputs(output_dir, "2026-08-01T00:00:00Z")
    current.write_text("second", encoding="utf-8")
    archive_existing_outputs(output_dir, "2026-08-01T00:00:00Z")

    history = output_dir / "history"
    assert (history / "2026-08-01T00-00-00Z" / current.name).read_text() == "first"
    assert (history / "2026-08-01T00-00-00Z-2" / current.name).read_text() == "second"


class _TransientSubreddit:
    display_name = "ApplyingToCollege"

    def __init__(self) -> None:
        self.attempts = 0

    @property
    def title(self) -> str:
        self.attempts += 1
        if self.attempts < 3:
            raise TooManyRequests("temporary auth probe failure")
        return "Applying to College"


class _AuthReddit:
    read_only = True
    auth = SimpleNamespace(limits={"used": 1, "remaining": 599})

    def __init__(self, subreddit: _TransientSubreddit) -> None:
        self._subreddit = subreddit

    def subreddit(self, name: str) -> _TransientSubreddit:
        assert name == "ApplyingToCollege"
        return self._subreddit


def _candidate(
    post_id: str,
    url: str,
    *,
    final_score: int,
    distinguished: str | None = None,
    stickied: bool = False,
) -> RedditCandidate:
    return RedditCandidate(
        reddit_post_id=post_id,
        fullname=f"t3_{post_id}",
        subreddit="ApplyingToCollege",
        title="Reusable College Application Advice",
        selftext="Detailed, durable admissions guidance with examples and concrete steps.",
        canonical_url=url,
        permalink=f"https://www.reddit.com/r/ApplyingToCollege/comments/{post_id}/guide",
        author_name="helpful_user",
        created_utc=1_710_000_000,
        retrieved_at="2026-08-01T00:00:00Z",
        score=100,
        upvote_ratio=0.95,
        num_comments=20,
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
        discovered_by=["listing:top:all"],
        external_url=url,
        summary="Reusable summary for applicants.",
        key_takeaways=["Use a checklist.", "Verify current requirements."],
        heuristic_score=final_score,
        final_usefulness_score=final_score,
        score_breakdown={
            "content_depth": {"score": 10},
            "engagement_signal": {"score": 5},
        },
    )
