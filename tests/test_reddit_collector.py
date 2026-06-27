import pytest

from src.collectors.reddit_collector import RedditCollector, _reddit_client
from src.config import RedditConfig


def test_reddit_client_requires_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("REDDIT_USER_AGENT", raising=False)

    client, error = _reddit_client()

    assert client is None
    assert error is not None
    assert "Missing Reddit API credentials" in error


class FakeAuthor:
    name = "college_user"


class FakeSubreddit:
    display_name = "ApplyingToCollege"


class FakeComment:
    author = FakeAuthor()
    body = "This comment adds practical essay advice."
    score = 12
    created_utc = 1710000000
    permalink = "/r/ApplyingToCollege/comments/post/comment"


class FakeComments(list):
    def replace_more(self, limit: int = 0) -> None:
        return None


class FakeSubmission:
    title = "How should I revise my college essay?"
    selftext = "I am a senior revising my Common App essay."
    permalink = "/r/ApplyingToCollege/comments/post"
    subreddit = FakeSubreddit()
    author = FakeAuthor()
    score = 42
    num_comments = 1
    created_utc = 1710000000
    comments = FakeComments([FakeComment()])


def test_reddit_submission_maps_to_source_item_schema() -> None:
    collector = RedditCollector(RedditConfig(max_comments=2))

    item = collector._item_from_submission(FakeSubmission(), "2026-06-26T00:00:00Z")
    item_dict = item.to_dict()

    assert item_dict["source_type"] == "reddit"
    assert item_dict["source_name"] == "r/ApplyingToCollege"
    assert item_dict["url"].startswith("https://www.reddit.com/")
    assert item_dict["title"] == "How should I revise my college essay?"
    assert item_dict["metadata"]["comments"][0]["body"] == (
        "This comment adds practical essay advice."
    )
