from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

import pytest

from src.reddit_discovery.atom_provider import (
    AtomRoute,
    CuratedSource,
    acquire_curated_a2c,
    acquire_manual_urls,
    acquire_rss,
    evenly_spaced,
    extract_curated_post_urls,
    parse_atom_candidates,
)
from src.reddit_discovery.options import RedditDiscoveryOptions, validate_options
from src.reddit_discovery.provider_http import BoundedHttpClient
from src.reddit_discovery.providers import (
    acquire_free_candidates,
    merge_discovery_results,
    resolve_provider,
)


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        body: str,
        *,
        content_type: str = "application/atom+xml; charset=UTF-8",
    ) -> None:
        self.status_code = status_code
        self.text = body
        self.headers = {"Content-Type": content_type}
        self.url = ""


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(url)
        if not self.responses:
            raise AssertionError("Unexpected HTTP request")
        response = self.responses.pop(0)
        response.url = url
        return response


def test_atom_parser_preserves_available_fields_and_marks_missing_engagement() -> None:
    body = _atom_feed(
        [
            _entry(
                "guide1",
                "Common App essay guide",
                "Start by brainstorming values, then draft and revise with feedback.",
                author=None,
            )
        ]
    )
    candidates, errors = parse_atom_candidates(
        body,
        discovered_by="rss_top_year",
        retrieved_at="2026-08-09T00:00:00Z",
        feed_url="https://www.reddit.com/r/ApplyingToCollege/top/.rss?t=year",
        acquisition_method="rss_atom",
    )

    assert errors == []
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.title == "Common App essay guide"
    assert candidate.author_name is None
    assert candidate.created_utc is None
    assert candidate.score is None
    assert candidate.num_comments is None
    assert candidate.provenance["engagement_fields_available"] is False
    assert "submitted by" not in candidate.selftext.casefold()
    assert candidate.discovered_by == ["rss_top_year"]


@pytest.mark.parametrize(
    "body",
    ["<feed>", "<html><body>not a feed</body></html>"],
)
def test_atom_parser_rejects_malformed_or_unexpected_xml(body: str) -> None:
    candidates, errors = parse_atom_candidates(
        body,
        discovered_by="rss_new",
        retrieved_at="2026-08-09T00:00:00Z",
        feed_url="https://www.reddit.com/feed",
        acquisition_method="rss_atom",
    )

    assert candidates == []
    assert errors[0].error_type == "AtomParseError"


def test_atom_parser_ignores_comments_and_duplicate_entries() -> None:
    post = _entry("same", "Essay advice", "Detailed reusable essay advice.")
    comment = _entry("comment1", "Re: Essay advice", "A comment", kind="t1")
    candidates, errors = parse_atom_candidates(
        _atom_feed([post, comment, post]),
        discovered_by="manual_url:1",
        retrieved_at="2026-08-09T00:00:00Z",
        feed_url="https://www.reddit.com/post/.rss",
        acquisition_method="manual_url",
    )

    assert errors == []
    assert [candidate.reddit_post_id for candidate in candidates] == ["same"]


def test_rss_provider_merges_duplicates_across_routes() -> None:
    first = _atom_feed([_entry("same", "Essay guide", "Reusable essay guidance.")])
    second = _atom_feed(
        [
            _entry("same", "Essay guide", "Reusable essay guidance."),
            _entry("aid", "Financial aid guide", "Compare FAFSA and aid offers carefully."),
        ]
    )
    session = FakeSession([FakeResponse(200, first), FakeResponse(200, second)])
    result = acquire_rss(
        _client(session),
        subreddit="ApplyingToCollege",
        candidate_limit=10,
        routes=(
            AtomRoute("rss_top_year", "https://www.reddit.com/top.rss"),
            AtomRoute("rss_new", "https://www.reddit.com/new.rss"),
        ),
    )

    assert {candidate.reddit_post_id for candidate in result.candidates} == {"same", "aid"}
    same = next(candidate for candidate in result.candidates if candidate.reddit_post_id == "same")
    assert same.discovered_by == ["rss_top_year", "rss_new"]
    assert result.duplicates == []


def test_rss_provider_stops_after_access_failure() -> None:
    session = FakeSession([FakeResponse(403, "denied", content_type="text/html")])
    result = acquire_rss(
        _client(session),
        subreddit="ApplyingToCollege",
        candidate_limit=10,
    )

    assert len(session.calls) == 1
    assert result.candidates == []
    assert result.errors[0].status_code == 403


def test_curated_link_extraction_deduplicates_and_rejects_irrelevant_links() -> None:
    first = "https://www.reddit.com/r/ApplyingToCollege/comments/abc123/guide/"
    html = (
        f'<a href="{first}">Guide</a>'
        f'<a href="{first}?utm_source=test">Duplicate</a>'
        '<a href="https://www.reddit.com/r/college/comments/other/post/">Wrong subreddit</a>'
        '<a href="https://example.com/article">External</a>'
        '<a href="not-a-url">Malformed</a>'
    )

    assert extract_curated_post_urls(html) == [first.rstrip("/")]
    assert evenly_spaced(["a", "b", "c", "d", "e"], 3) == ["a", "b", "d"]


def test_curated_provider_fetches_details_with_provenance() -> None:
    detail_url = "https://www.reddit.com/r/ApplyingToCollege/comments/detail1/guide/"
    source_body = f'<p>Organized resources</p><a href="{detail_url}">Essay guide</a>'
    source_feed = _atom_feed(
        [_entry("master", "Organized A2C resources", source_body, raw_html=True)]
    )
    detail_feed = _atom_feed(
        [_entry("detail1", "Detailed essay guide", "Brainstorm, draft, and revise in stages.")]
    )
    session = FakeSession([FakeResponse(200, source_feed), FakeResponse(200, detail_feed)])
    result = acquire_curated_a2c(
        _client(session),
        candidate_limit=5,
        detail_limit=1,
        sources=(
            CuratedSource(
                "test_masterpost",
                "https://www.reddit.com/r/ApplyingToCollege/comments/master/resources/",
            ),
        ),
    )

    assert {candidate.reddit_post_id for candidate in result.candidates} == {
        "master",
        "detail1",
    }
    detail = next(candidate for candidate in result.candidates if candidate.reddit_post_id == "detail1")
    assert detail.acquisition_method == "curated_a2c"
    assert detail.provenance["curated_source"] == "test_masterpost"
    assert detail.provenance["curated_post_url"] == detail_url.rstrip("/")


def test_curated_provider_marks_rate_limited_detail_run_incomplete() -> None:
    detail_url = "https://www.reddit.com/r/ApplyingToCollege/comments/detail1/guide/"
    source_feed = _atom_feed(
        [
            _entry(
                "master",
                "Organized A2C resources",
                f'<a href="{detail_url}">Essay guide</a>',
                raw_html=True,
            )
        ]
    )
    session = FakeSession(
        [
            FakeResponse(200, source_feed),
            FakeResponse(429, "", content_type="text/plain"),
        ]
    )
    result = acquire_curated_a2c(
        _client(session),
        candidate_limit=5,
        detail_limit=1,
        sources=(
            CuratedSource(
                "test_masterpost",
                "https://www.reddit.com/r/ApplyingToCollege/comments/master/resources/",
            ),
        ),
    )

    assert [candidate.reddit_post_id for candidate in result.candidates] == ["master"]
    assert result.completed_routes == []
    assert result.errors[0].status_code == 429


def test_manual_urls_validate_scope_and_avoid_duplicate_requests() -> None:
    valid = "https://www.reddit.com/r/ApplyingToCollege/comments/manual1/guide/"
    session = FakeSession(
        [FakeResponse(200, _atom_feed([_entry("manual1", "Essay guide", "Useful steps.")]))]
    )
    result = acquire_manual_urls(
        _client(session),
        urls=["https://example.com/no", valid, f"{valid}?utm_source=test"],
        candidate_limit=5,
    )

    assert len(session.calls) == 1
    assert [candidate.reddit_post_id for candidate in result.candidates] == ["manual1"]
    assert result.errors[0].error_type == "AtomParseError"
    assert result.candidates[0].provenance["manual_source_url"] == valid.rstrip("/")


def test_auto_provider_falls_back_from_curated_failure_to_rss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.reddit_discovery.providers.praw_credentials_available",
        lambda: False,
    )
    rss_feed = _atom_feed(
        [_entry("rss1", "Application timeline guide", "Plan each application deadline.")]
    )
    session = FakeSession(
        [
            FakeResponse(403, "denied", content_type="text/html"),
            FakeResponse(200, rss_feed),
            FakeResponse(200, rss_feed),
        ]
    )
    options = RedditDiscoveryOptions(
        provider="auto",
        output_dir=tmp_path,
        candidate_limit=10,
    )
    acquisition = acquire_free_candidates(
        options,
        retrieved_at="2026-08-09T00:00:00Z",
        client=_client(session),
    )

    assert [status.provider for status in acquisition.statuses] == ["curated", "rss"]
    assert acquisition.statuses[0].status == "unavailable"
    assert acquisition.statuses[1].status == "completed"
    assert [candidate.reddit_post_id for candidate in acquisition.discovery.candidates] == ["rss1"]


def test_provider_composition_preserves_provenance_without_duplicate_candidates() -> None:
    first, _ = parse_atom_candidates(
        _atom_feed([_entry("same", "Essay guide", "Useful essay steps.")]),
        discovered_by="curated_a2c:test",
        retrieved_at="2026-08-09T00:00:00Z",
        feed_url="https://www.reddit.com/curated.rss",
        acquisition_method="curated_a2c",
    )
    second, _ = parse_atom_candidates(
        _atom_feed([_entry("same", "Essay guide", "Useful essay steps.")]),
        discovered_by="rss_top_year",
        retrieved_at="2026-08-09T00:00:00Z",
        feed_url="https://www.reddit.com/top.rss",
        acquisition_method="rss_atom",
    )
    from src.reddit_discovery.models import DiscoveryResult

    merged = merge_discovery_results(
        [DiscoveryResult(candidates=first), DiscoveryResult(candidates=second)],
        candidate_limit=5,
    )

    assert len(merged.candidates) == 1
    assert merged.candidates[0].discovered_by == ["curated_a2c:test", "rss_top_year"]


def test_auto_resolution_prefers_praw_only_when_credentials_are_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = RedditDiscoveryOptions(provider="auto")
    monkeypatch.setattr(
        "src.reddit_discovery.providers.praw_credentials_available",
        lambda: False,
    )
    assert resolve_provider(options) == "free-auto"
    monkeypatch.setattr(
        "src.reddit_discovery.providers.praw_credentials_available",
        lambda: True,
    )
    assert resolve_provider(options) == "praw"


def test_manual_provider_configuration_requires_urls() -> None:
    with pytest.raises(ValueError, match="requires at least one"):
        validate_options(RedditDiscoveryOptions(provider="manual"))
    with pytest.raises(ValueError, match="requires --provider manual"):
        validate_options(
            RedditDiscoveryOptions(provider="rss", reddit_urls=("https://example.com",))
        )


def _client(session: FakeSession) -> BoundedHttpClient:
    return BoundedHttpClient(
        request_limit=20,
        cache_dir=None,
        session=session,
        max_retries=0,
        sleep_fn=lambda _: None,
    )


def _atom_feed(entries: list[str]) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        + "".join(entries)
        + "</feed>"
    )


def _entry(
    post_id: str,
    title: str,
    body: str,
    *,
    author: str | None = "helpful_user",
    kind: str = "t3",
    raw_html: bool = False,
) -> str:
    post_url = f"https://www.reddit.com/r/ApplyingToCollege/comments/{post_id}/guide/"
    content = body if raw_html else f"<p>{body}</p>"
    content += (
        f'<p>submitted by <a href="https://www.reddit.com/user/{author or "deleted"}">'
        f'{"/u/" + author if author else "[deleted]"}</a> '
        f'<a href="{post_url}">[link]</a> '
        f'<a href="{post_url}">[comments]</a></p>'
    )
    author_xml = (
        f"<author><name>/u/{escape(author)}</name></author>" if author else ""
    )
    return (
        f"<entry><id>{kind}_{escape(post_id)}</id><title>{escape(title)}</title>"
        f"<updated>2026-08-01T00:00:00Z</updated>{author_xml}"
        f'<content type="html">{escape(content)}</content></entry>'
    )
