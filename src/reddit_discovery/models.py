from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from src.reddit_discovery.constants import PIPELINE_VERSION


@dataclass(slots=True)
class RedditComment:
    comment_id: str
    author: str | None
    body: str
    score: int | None
    created_at: str
    permalink: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RedditComment:
        return cls(
            comment_id=_string(data.get("comment_id")),
            author=_optional_string(data.get("author")),
            body=_string(data.get("body")),
            score=_integer(data.get("score")),
            created_at=_string(data.get("created_at")),
            permalink=_string(data.get("permalink")),
        )


@dataclass(slots=True)
class RedditCandidate:
    reddit_post_id: str
    fullname: str
    subreddit: str
    title: str
    selftext: str
    canonical_url: str
    permalink: str
    author_name: str | None
    created_utc: float | None
    retrieved_at: str
    score: int
    upvote_ratio: float | None
    num_comments: int | None
    link_flair_text: str | None
    is_self: bool
    is_original_content: bool
    over_18: bool
    spoiler: bool
    stickied: bool
    distinguished: str | None
    locked: bool
    archived: bool
    removed_by_category: str | None
    acquisition_method: str = "reddit_api"
    provenance: dict[str, Any] = field(default_factory=dict)
    discovered_by: list[str] = field(default_factory=list)
    external_url: str | None = None
    comments: list[RedditComment] = field(default_factory=list)
    content_hash: str = ""
    hard_rejection_reason: str | None = None
    rejection_reason: str | None = None
    primary_topic: str = "other"
    secondary_topics: list[str] = field(default_factory=list)
    audience: list[str] = field(default_factory=list)
    summary: str = ""
    key_takeaways: list[str] = field(default_factory=list)
    why_useful: str = ""
    limitations_or_cautions: list[str] = field(default_factory=list)
    freshness_status: str = "durable"
    heuristic_score: int = 0
    llm_adjustment: int = 0
    final_usefulness_score: int = 0
    score_breakdown: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    requires_human_review: bool = False
    selection_reasons: list[str] = field(default_factory=list)
    resource_id: str | None = None
    duplicate_of: str | None = None
    processing_status: str = "discovered"

    def __post_init__(self) -> None:
        self.discovered_by = _unique_strings(self.discovered_by)
        self.secondary_topics = _unique_strings(self.secondary_topics)
        self.audience = _unique_strings(self.audience)
        self.selection_reasons = _unique_strings(self.selection_reasons)
        if not self.content_hash:
            self.refresh_content_hash()

    @property
    def content_depth(self) -> int:
        value = self.score_breakdown.get("content_depth")
        if isinstance(value, Mapping):
            return _integer(value.get("score"))
        return _integer(value)

    @property
    def created_at(self) -> str:
        return utc_timestamp_to_iso(self.created_utc)

    def merge_discovered_by(self, origins: Iterable[str]) -> None:
        self.discovered_by = _unique_strings([*self.discovered_by, *origins])

    def refresh_content_hash(self) -> str:
        self.content_hash = candidate_content_hash(self)
        return self.content_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "reddit_post_id": self.reddit_post_id,
            "fullname": self.fullname,
            "subreddit": self.subreddit,
            "title": self.title,
            "selftext": self.selftext,
            "canonical_url": self.canonical_url,
            "permalink": self.permalink,
            "author_name": self.author_name,
            "created_utc": self.created_utc,
            "retrieved_at": self.retrieved_at,
            "score": self.score,
            "upvote_ratio": self.upvote_ratio,
            "num_comments": self.num_comments,
            "link_flair_text": self.link_flair_text,
            "is_self": self.is_self,
            "is_original_content": self.is_original_content,
            "over_18": self.over_18,
            "spoiler": self.spoiler,
            "stickied": self.stickied,
            "distinguished": self.distinguished,
            "locked": self.locked,
            "archived": self.archived,
            "removed_by_category": self.removed_by_category,
            "acquisition_method": self.acquisition_method,
            "provenance": dict(self.provenance),
            "discovered_by": list(self.discovered_by),
            "external_url": self.external_url,
            "comments": [comment.to_dict() for comment in self.comments],
            "content_hash": self.content_hash,
            "hard_rejection_reason": self.hard_rejection_reason,
            "rejection_reason": self.rejection_reason,
            "primary_topic": self.primary_topic,
            "secondary_topics": list(self.secondary_topics),
            "audience": list(self.audience),
            "summary": self.summary,
            "key_takeaways": list(self.key_takeaways),
            "why_useful": self.why_useful,
            "limitations_or_cautions": list(self.limitations_or_cautions),
            "freshness_status": self.freshness_status,
            "heuristic_score": self.heuristic_score,
            "llm_adjustment": self.llm_adjustment,
            "final_usefulness_score": self.final_usefulness_score,
            "score_breakdown": dict(self.score_breakdown),
            "confidence": self.confidence,
            "requires_human_review": self.requires_human_review,
            "selection_reasons": list(self.selection_reasons),
            "resource_id": self.resource_id,
            "duplicate_of": self.duplicate_of,
            "processing_status": self.processing_status,
        }

    def to_resource_dict(self, pipeline_version: str = PIPELINE_VERSION) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id or f"reddit_{self.reddit_post_id}",
            "source_platform": "reddit",
            "subreddit": self.subreddit,
            "reddit_post_id": self.reddit_post_id,
            "canonical_url": self.canonical_url,
            "permalink": self.permalink,
            "external_url": self.external_url,
            "title": self.title,
            "cleaned_text": self.selftext,
            "author": self.author_name,
            "created_at": self.created_at,
            "retrieved_at": self.retrieved_at,
            "score": self.score,
            "upvote_ratio": self.upvote_ratio,
            "comment_count": self.num_comments,
            "flair": self.link_flair_text,
            "primary_topic": self.primary_topic,
            "secondary_topics": list(self.secondary_topics),
            "audience": list(self.audience),
            "summary": self.summary,
            "key_takeaways": list(self.key_takeaways),
            "why_useful": self.why_useful,
            "limitations_or_cautions": list(self.limitations_or_cautions),
            "freshness_status": self.freshness_status,
            "heuristic_score": self.heuristic_score,
            "llm_adjustment": self.llm_adjustment,
            "final_usefulness_score": self.final_usefulness_score,
            "score_breakdown": dict(self.score_breakdown),
            "confidence": self.confidence,
            "selection_reasons": list(self.selection_reasons),
            "discovered_by": list(self.discovered_by),
            "acquisition_method": self.acquisition_method,
            "provenance": dict(self.provenance),
            "content_hash": self.content_hash,
            "pipeline_version": pipeline_version,
            "comments": [comment.to_dict() for comment in self.comments],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RedditCandidate:
        comments = data.get("comments", [])
        return cls(
            reddit_post_id=_string(data.get("reddit_post_id")),
            fullname=_string(data.get("fullname")),
            subreddit=_string(data.get("subreddit")),
            title=_string(data.get("title")),
            selftext=_string(data.get("selftext")),
            canonical_url=_string(data.get("canonical_url")),
            permalink=_string(data.get("permalink")),
            author_name=_optional_string(data.get("author_name")),
            created_utc=_optional_float(data.get("created_utc")),
            retrieved_at=_string(data.get("retrieved_at")),
            score=_optional_integer(data.get("score")),
            upvote_ratio=_optional_float(data.get("upvote_ratio")),
            num_comments=_optional_integer(data.get("num_comments")),
            link_flair_text=_optional_string(data.get("link_flair_text")),
            is_self=bool(data.get("is_self", False)),
            is_original_content=bool(data.get("is_original_content", False)),
            over_18=bool(data.get("over_18", False)),
            spoiler=bool(data.get("spoiler", False)),
            stickied=bool(data.get("stickied", False)),
            distinguished=_optional_string(data.get("distinguished")),
            locked=bool(data.get("locked", False)),
            archived=bool(data.get("archived", False)),
            removed_by_category=_optional_string(data.get("removed_by_category")),
            acquisition_method=_string(data.get("acquisition_method")) or "reddit_api",
            provenance=_mapping_copy(data.get("provenance")),
            discovered_by=_string_list(data.get("discovered_by")),
            external_url=_optional_string(data.get("external_url")),
            comments=[
                RedditComment.from_dict(comment)
                for comment in comments
                if isinstance(comment, Mapping)
            ] if isinstance(comments, list) else [],
            content_hash=_string(data.get("content_hash")),
            hard_rejection_reason=_optional_string(data.get("hard_rejection_reason")),
            rejection_reason=_optional_string(data.get("rejection_reason")),
            primary_topic=_string(data.get("primary_topic")) or "other",
            secondary_topics=_string_list(data.get("secondary_topics")),
            audience=_string_list(data.get("audience")),
            summary=_string(data.get("summary")),
            key_takeaways=_string_list(data.get("key_takeaways")),
            why_useful=_string(data.get("why_useful")),
            limitations_or_cautions=_string_list(data.get("limitations_or_cautions")),
            freshness_status=_string(data.get("freshness_status")) or "durable",
            heuristic_score=_integer(data.get("heuristic_score")),
            llm_adjustment=_integer(data.get("llm_adjustment")),
            final_usefulness_score=_integer(data.get("final_usefulness_score")),
            score_breakdown=_mapping_copy(data.get("score_breakdown")),
            confidence=_float(data.get("confidence")),
            requires_human_review=bool(data.get("requires_human_review", False)),
            selection_reasons=_string_list(data.get("selection_reasons")),
            resource_id=_optional_string(data.get("resource_id")),
            duplicate_of=_optional_string(data.get("duplicate_of")),
            processing_status=_string(data.get("processing_status")) or "discovered",
        )


@dataclass(slots=True)
class DiscoveryError:
    route: str
    error_type: str
    message: str
    retryable: bool
    attempts: int
    status_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DiscoveryResult:
    candidates: list[RedditCandidate] = field(default_factory=list)
    duplicates: list[RedditCandidate] = field(default_factory=list)
    errors: list[DiscoveryError] = field(default_factory=list)
    route_counts: dict[str, int] = field(default_factory=dict)
    completed_routes: list[str] = field(default_factory=list)
    limit_reached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "duplicates": [candidate.to_dict() for candidate in self.duplicates],
            "errors": [error.to_dict() for error in self.errors],
            "route_counts": dict(self.route_counts),
            "completed_routes": list(self.completed_routes),
            "limit_reached": self.limit_reached,
        }


@dataclass(slots=True)
class AuthProbeResult:
    success: bool
    read_only: bool
    subreddit: str
    subreddit_title: str
    accessible: bool
    rate_limit: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    next_step: str = ""


def candidate_content_hash(candidate: RedditCandidate) -> str:
    payload = {
        "reddit_post_id": candidate.reddit_post_id,
        "title": candidate.title.strip(),
        "selftext": candidate.selftext.strip(),
        "external_url": (candidate.external_url or "").strip(),
        "link_flair_text": candidate.link_flair_text,
        "removed_by_category": candidate.removed_by_category,
        "score": candidate.score,
        "upvote_ratio": candidate.upvote_ratio,
        "num_comments": candidate.num_comments,
        "stickied": candidate.stickied,
        "distinguished": candidate.distinguished,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def utc_timestamp_to_iso(value: float | None) -> str:
    if value is None:
        return ""
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
    except (OSError, OverflowError, TypeError, ValueError):
        return ""


def _string(value: Any) -> str:
    return "" if value is None else str(value)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _optional_integer(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return _unique_strings(value)


def _unique_strings(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _mapping_copy(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}
