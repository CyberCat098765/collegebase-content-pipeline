from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlparse


class RejectionCode(str, Enum):
    EMPTY_TITLE = "EMPTY_TITLE"
    REMOVED_CONTENT = "REMOVED_CONTENT"
    DELETED_CONTENT = "DELETED_CONTENT"
    MISSING_CONTENT = "MISSING_CONTENT"
    BLOCKED_CATEGORY_CHANCE_ME = "BLOCKED_CATEGORY_CHANCE_ME"
    BLOCKED_CATEGORY_RESULTS = "BLOCKED_CATEGORY_RESULTS"
    BLOCKED_CATEGORY_MEME = "BLOCKED_CATEGORY_MEME"
    BLOCKED_CATEGORY_FLUFF = "BLOCKED_CATEGORY_FLUFF"
    BLOCKED_CATEGORY_CELEBRATION = "BLOCKED_CATEGORY_CELEBRATION"
    BLOCKED_CATEGORY_SELF_PROMOTION = "BLOCKED_CATEGORY_SELF_PROMOTION"
    TRIVIAL_TEST_SCORE_QUESTION = "TRIVIAL_TEST_SCORE_QUESTION"
    LOW_INFORMATION = "LOW_INFORMATION"
    NARROW_PERSONAL_QUESTION = "NARROW_PERSONAL_QUESTION"


@dataclass(frozen=True, slots=True)
class HardFilterResult:
    passed: bool
    reason_code: str | None = None
    explanation: str = ""

    @property
    def rejected(self) -> bool:
        return not self.passed

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "reason_code": self.reason_code,
            "explanation": self.explanation,
        }


_BLOCKED_TITLE_FLAIR_RULES: tuple[tuple[RejectionCode, tuple[str, ...]], ...] = (
    (
        RejectionCode.BLOCKED_CATEGORY_CHANCE_ME,
        (
            "chance me",
            "reverse chance me",
            "roast my application",
            "rate my application",
            "predict my results",
        ),
    ),
    (
        RejectionCode.BLOCKED_CATEGORY_RESULTS,
        (
            "college results",
            "results day",
            "acceptance reaction",
            "rejection reaction",
            "decision reaction",
            "make assumptions about me",
            "guess where i got in",
        ),
    ),
    (
        RejectionCode.BLOCKED_CATEGORY_MEME,
        ("meme", "shitpost"),
    ),
    (
        RejectionCode.BLOCKED_CATEGORY_FLUFF,
        (
            "hype",
            "manifest",
            "manifestation",
            "fluff",
            "rant",
            "vent",
            "off topic",
        ),
    ),
)

_RESOURCE_TITLE_SIGNALS = (
    "guide",
    "resource",
    "faq",
    "megathread",
    "ama",
)

_REUSABLE_TEACHING_SIGNALS = (
    "guide",
    "checklist",
    "step by step",
    "what i learned",
    "lessons learned",
    "things i wish i knew",
    "tips for applicants",
    "tips for students",
    "advice for applicants",
    "advice for students",
    "for future applicants",
    "for anyone applying",
)

_NARROW_PERSONAL_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bmy\s+gpa\b",
        r"\bmy\s+(?:exact\s+)?(?:sat|act)\s+score\b",
        r"\bwhich\s+(?:one\s+)?school\s+should\s+i\s+choose\b",
        r"\bwill\s+(?:one|a)\s+b\b.{0,80}\b(?:ruin|hurt)\b",
        r"\bam\s+i\s+cooked\b",
        r"\bcan\s+i\s+get\s+into\b",
        r"\bshould\s+i\s+apply\s+to\s+(?:this|one|the)\b",
    )
)

_TRIVIAL_SCORE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bis\s+(?:a\s+)?\d{2,4}\s+(?:sat|act)?\s*(?:score\s+)?good\b",
        r"\bshould\s+i\s+submit\s+(?:my\s+)?\d{2,4}\b",
        r"\b(?:sat|act)\s*[:=-]?\s*\d{2,4}\s+(?:good|bad|enough)\b",
    )
)

_CELEBRATION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^\s*(?:omg\s+)?i\s+(?:just\s+)?(?:got\s+in|got\s+accepted|was\s+accepted|was\s+rejected)\b",
        r"^\s*(?:accepted|rejected|waitlisted)(?:!+|\?+)?\s*$",
    )
)

_SELF_PROMOTION_SIGNALS = (
    "paid service", "essay editing service", "admissions consulting",
    "book a consultation", "dm me for", "use my referral", "use my code",
    "subscribe to my channel", "join my discord", "buy my course",
)

_UNAVAILABLE_BODIES = {"[deleted]", "[removed]"}
_REDDIT_HOSTS = {
    "reddit.com",
    "www.reddit.com",
    "old.reddit.com",
    "new.reddit.com",
    "redd.it",
    "i.redd.it",
    "v.redd.it",
}
_MEDIA_EXTENSIONS = (".gif", ".gifv", ".jpeg", ".jpg", ".mov", ".mp4", ".png", ".webp")


def evaluate_hard_filters(candidate: object) -> HardFilterResult:
    """Apply stable post-level rejection rules before comments or any LLM work."""

    title = _text(_field(candidate, "title"))
    body = _text(_field(candidate, "selftext"))
    body_marker = body.casefold()

    if _is_deleted(candidate, body_marker):
        return _reject(RejectionCode.DELETED_CONTENT, "The submission body was deleted.")
    if _is_removed(candidate, body_marker):
        return _reject(RejectionCode.REMOVED_CONTENT, "Reddit marks the submission as removed.")
    if not title:
        return _reject(RejectionCode.EMPTY_TITLE, "The submission has no title.")

    category = blocked_category(title, _text(_field(candidate, "link_flair_text")))
    if category is not None:
        return _reject(category, "The title or flair is an explicitly excluded category.")

    if any(pattern.search(title) for pattern in _TRIVIAL_SCORE_PATTERNS):
        return _reject(
            RejectionCode.TRIVIAL_TEST_SCORE_QUESTION,
            "The post asks a repetitive personal test-score question.",
        )
    if any(pattern.search(title) for pattern in _CELEBRATION_PATTERNS):
        return _reject(
            RejectionCode.BLOCKED_CATEGORY_CELEBRATION,
            "The post is an individual decision reaction without reusable guidance.",
        )
    if is_self_promotion(title, body):
        return _reject(
            RejectionCode.BLOCKED_CATEGORY_SELF_PROMOTION,
            "The post primarily promotes a paid service, channel, referral, or community.",
        )

    if is_narrow_personal_question(title, body):
        return _reject(
            RejectionCode.NARROW_PERSONAL_QUESTION,
            "The post asks a narrowly personal question without reusable guidance.",
        )

    external_context = has_useful_external_link(candidate)
    usable_body = "" if body_marker in _UNAVAILABLE_BODIES else body
    if not usable_body and not external_context:
        return _reject(
            RejectionCode.MISSING_CONTENT,
            "Neither a usable submission body nor useful external context is available.",
        )

    if (
        len(usable_body) < 250
        and not _as_bool(_field(candidate, "stickied"))
        and not _is_distinguished(_field(candidate, "distinguished"))
        and not title_has_resource_signal(title)
        and not external_context
    ):
        return _reject(
            RejectionCode.LOW_INFORMATION,
            "The post is short and has no moderator, resource-title, or external-link signal.",
        )

    return HardFilterResult(passed=True)


# Readable aliases for callers and tests.
hard_filter_candidate = evaluate_hard_filters
apply_hard_filters = evaluate_hard_filters


def blocked_category(title: str, flair: str = "") -> RejectionCode | None:
    for value in (title, flair):
        normalized = _normalize_for_phrase_match(value)
        for code, phrases in _BLOCKED_TITLE_FLAIR_RULES:
            if any(_contains_phrase(normalized, phrase) for phrase in phrases):
                return code
    return None


def title_has_resource_signal(title: str) -> bool:
    normalized = _normalize_for_phrase_match(title)
    return any(_contains_phrase(normalized, signal) for signal in _RESOURCE_TITLE_SIGNALS)


def is_narrow_personal_question(title: str, body: str) -> bool:
    combined = f"{title}\n{body}".strip()
    lowered = _normalize_for_phrase_match(combined)
    if any(signal in lowered for signal in _REUSABLE_TEACHING_SIGNALS):
        return False
    if len(body) >= 600 and _has_broadly_actionable_structure(body):
        return False
    return any(pattern.search(combined) for pattern in _NARROW_PERSONAL_PATTERNS)


def is_self_promotion(title: str, body: str) -> bool:
    normalized = _normalize_for_phrase_match(f"{title}\n{body}")
    return any(_contains_phrase(normalized, signal) for signal in _SELF_PROMOTION_SIGNALS)


def has_useful_external_link(candidate: object) -> bool:
    explicit = _field(candidate, "has_useful_external_context", None)
    if explicit is not None:
        return _as_bool(explicit)

    for field_name in ("external_url", "outbound_url", "canonical_url", "url"):
        value = _text(_field(candidate, field_name))
        parsed = urlparse(value)
        host = (parsed.hostname or "").casefold()
        if parsed.scheme not in {"http", "https"} or not host:
            continue
        if host in _REDDIT_HOSTS or host.endswith(".reddit.com"):
            continue
        if parsed.path.casefold().endswith(_MEDIA_EXTENSIONS):
            continue
        return True
    return False


def is_curated_resource(candidate: object) -> bool:
    acquisition_method = _text(_field(candidate, "acquisition_method")).casefold()
    title = _normalize_for_phrase_match(_text(_field(candidate, "title")))
    primary_topic = _text(_field(candidate, "primary_topic")).casefold()
    resource_markers = ("guide", "resource", "faq", "masterpost")
    return (
        acquisition_method == "curated_a2c"
        and primary_topic != "other"
        and any(_contains_phrase(title, marker) for marker in resource_markers)
    )


def comment_rejection_code(body: object, author: object) -> str | None:
    cleaned = _text(body)
    if cleaned.casefold() in _UNAVAILABLE_BODIES or not cleaned:
        return "COMMENT_DELETED_OR_REMOVED"
    if _text(author).casefold() == "automoderator":
        return "COMMENT_AUTOMODERATOR"
    if len(cleaned) < 80:
        return "COMMENT_TOO_SHORT"
    if _is_reaction_only(cleaned):
        return "COMMENT_REACTION_ONLY"
    return None


def is_comment_usable(body: object, author: object) -> bool:
    return comment_rejection_code(body, author) is None


def _is_removed(candidate: object, body_marker: str) -> bool:
    removed_by = _field(candidate, "removed_by_category", None)
    return body_marker == "[removed]" or (
        removed_by is not None and _text(removed_by).casefold() not in {"", "none", "deleted"}
    )


def _is_deleted(candidate: object, body_marker: str) -> bool:
    return body_marker == "[deleted]" or _text(
        _field(candidate, "removed_by_category", None)
    ).casefold() == "deleted"


def _has_broadly_actionable_structure(body: str) -> bool:
    lowered = body.casefold()
    markers = sum(
        marker in lowered
        for marker in ("first", "next", "then", "example", "checklist", "step", "avoid")
    )
    list_items = len(re.findall(r"(?m)^\s*(?:[-*\u2022]|\d+[.)])\s+", body))
    return markers >= 2 or list_items >= 3


def _is_reaction_only(body: str) -> bool:
    words = re.findall(r"[a-z0-9']+", body.casefold())
    if len(words) > 14:
        return False
    normalized = " ".join(words)
    reaction_phrases = (
        "lol",
        "lmao",
        "this is hilarious",
        "same",
        "congrats",
        "congratulations",
        "you got this",
        "big w",
        "huge w",
    )
    return any(_contains_phrase(normalized, phrase) for phrase in reaction_phrases) or bool(
        re.fullmatch(r"[\W_]+", body)
    )


def _reject(code: RejectionCode, explanation: str) -> HardFilterResult:
    return HardFilterResult(False, code.value, explanation)


def _normalize_for_phrase_match(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.casefold())).strip()


def _contains_phrase(normalized_text: str, phrase: str) -> bool:
    normalized_phrase = _normalize_for_phrase_match(phrase)
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(normalized_phrase)}(?![a-z0-9])", normalized_text))


def _is_distinguished(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return bool(_text(value))


def _as_bool(value: object) -> bool:
    if isinstance(value, str):
        return value.casefold() in {"1", "true", "yes", "on"}
    return bool(value)


def _field(candidate: object, name: str, default: Any = "") -> Any:
    if isinstance(candidate, Mapping):
        return candidate.get(name, default)
    return getattr(candidate, name, default)


def _text(value: object) -> str:
    return str(value or "").strip()
