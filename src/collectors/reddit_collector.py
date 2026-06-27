from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from src.config import RedditConfig
from src.models import PipelineError, SourceItem
from src.processing.cleaner import clean_text, clean_title

REDDIT_BASE_URL = "https://www.reddit.com"
REDDIT_CLIENT_ID_ENV = "REDDIT_CLIENT_ID"
REDDIT_CLIENT_SECRET_ENV = "REDDIT_CLIENT_SECRET"
REDDIT_USER_AGENT_ENV = "REDDIT_USER_AGENT"


class RedditCollector:
    def __init__(self, config: RedditConfig) -> None:
        self.config = config

    def collect(self, collected_at: str) -> tuple[list[SourceItem], list[PipelineError]]:
        if not self.config.urls and not (self.config.subreddits and self.config.keywords):
            return [], []

        reddit, auth_error = _reddit_client()
        if auth_error:
            return [], [PipelineError("reddit", "Reddit API", auth_error)]

        items: list[SourceItem] = []
        errors: list[PipelineError] = []

        for url in self.config.urls:
            item, error = self._collect_direct_url(reddit, url, collected_at)
            if item:
                items.append(item)
            if error:
                errors.append(error)

        for subreddit in self.config.subreddits:
            for keyword in self.config.keywords:
                found, query_errors = self._search_subreddit(
                    reddit=reddit,
                    subreddit=subreddit,
                    keyword=keyword,
                    collected_at=collected_at,
                )
                items.extend(found)
                errors.extend(query_errors)

        return items, errors

    def _collect_direct_url(
        self, reddit: Any, url: str, collected_at: str
    ) -> tuple[SourceItem | None, PipelineError | None]:
        host = urlparse(url).netloc.lower()
        if host not in {"reddit.com", "www.reddit.com"} and not host.endswith(".reddit.com"):
            return None, PipelineError("reddit", url, "Invalid Reddit URL.")

        try:
            submission = reddit.submission(url=url)
            item = self._item_from_submission(submission, collected_at)
        except Exception as exc:
            return None, PipelineError("reddit", url, _reddit_error_message(exc))

        return item, None

    def _search_subreddit(
        self,
        reddit: Any,
        subreddit: str,
        keyword: str,
        collected_at: str,
    ) -> tuple[list[SourceItem], list[PipelineError]]:
        items: list[SourceItem] = []
        errors: list[PipelineError] = []

        try:
            submissions = reddit.subreddit(subreddit).search(
                keyword,
                sort="relevance",
                limit=self.config.limit_per_query,
            )
            for submission in submissions:
                items.append(
                    self._item_from_submission(
                        submission=submission,
                        collected_at=collected_at,
                        search_keyword=keyword,
                    )
                )
        except Exception as exc:
            errors.append(
                PipelineError(
                    source_type="reddit",
                    source=f"r/{subreddit}:{keyword}",
                    message=_reddit_error_message(exc),
                )
            )

        return items, errors

    def _item_from_submission(
        self,
        submission: Any,
        collected_at: str,
        search_keyword: str = "",
    ) -> SourceItem:
        title = clean_title(str(getattr(submission, "title", "")))
        source_url = _submission_permalink(submission)
        comments = self._submission_comments(submission)
        body = clean_text(str(getattr(submission, "selftext", "") or ""))
        comment_texts = [clean_text(str(comment.get("body", ""))) for comment in comments]
        raw_text = clean_text("\n\n".join(part for part in [title, body, *comment_texts] if part))

        metadata: dict[str, Any] = {
            "subreddit": _subreddit_name(submission),
            "score": getattr(submission, "score", None),
            "permalink": source_url,
            "num_comments": getattr(submission, "num_comments", None),
            "comments": comments,
        }
        if search_keyword:
            metadata["search_keyword"] = search_keyword

        return SourceItem(
            source_type="reddit",
            source_name=f"r/{_subreddit_name(submission)}",
            title=title,
            url=source_url,
            author_or_channel=_author_name(getattr(submission, "author", None)),
            published_date=_utc_from_timestamp(getattr(submission, "created_utc", None)),
            collected_at=collected_at,
            raw_text=raw_text,
            metadata=metadata,
        )

    def _submission_comments(self, submission: Any) -> list[dict[str, Any]]:
        comments: list[dict[str, Any]] = []
        try:
            submission.comment_sort = "top"
            submission.comments.replace_more(limit=0)
            top_level_comments = submission.comments[: self.config.max_comments]
        except Exception:
            return comments

        for comment in top_level_comments:
            body = clean_text(str(getattr(comment, "body", "") or ""))
            if not body or body in {"[deleted]", "[removed]"}:
                continue
            comments.append(
                {
                    "author": _author_name(getattr(comment, "author", None)),
                    "body": body,
                    "score": getattr(comment, "score", None),
                    "created_date": _utc_from_timestamp(getattr(comment, "created_utc", None)),
                    "permalink": _comment_permalink(comment),
                }
            )

        return comments


def _reddit_client() -> tuple[Any | None, str | None]:
    client_id = os.getenv(REDDIT_CLIENT_ID_ENV, "").strip()
    client_secret = os.getenv(REDDIT_CLIENT_SECRET_ENV, "").strip()
    user_agent = os.getenv(REDDIT_USER_AGENT_ENV, "").strip()

    if not client_id or not client_secret or not user_agent:
        return (
            None,
            "Missing Reddit API credentials. Set REDDIT_CLIENT_ID, "
            "REDDIT_CLIENT_SECRET, and REDDIT_USER_AGENT to enable Reddit collection.",
        )

    try:
        import praw
    except ImportError:
        return None, "PRAW is not installed."

    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
        check_for_async=False,
    )
    reddit.read_only = True
    return reddit, None


def _submission_permalink(submission: Any) -> str:
    permalink = str(getattr(submission, "permalink", "") or "")
    if permalink.startswith("/"):
        return f"{REDDIT_BASE_URL}{permalink}"
    return permalink or str(getattr(submission, "url", "") or "")


def _comment_permalink(comment: Any) -> str:
    permalink = str(getattr(comment, "permalink", "") or "")
    if permalink.startswith("/"):
        return f"{REDDIT_BASE_URL}{permalink}"
    return permalink


def _subreddit_name(submission: Any) -> str:
    subreddit = getattr(submission, "subreddit", "")
    display_name = getattr(subreddit, "display_name", "")
    return str(display_name or subreddit or "")


def _author_name(author: Any) -> str:
    return str(getattr(author, "name", "") or author or "")


def _utc_from_timestamp(value: object) -> str:
    if value in {None, ""}:
        return ""
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def _reddit_error_message(error: Exception) -> str:
    error_type = type(error).__name__
    return f"Reddit API request failed: {error_type}"
