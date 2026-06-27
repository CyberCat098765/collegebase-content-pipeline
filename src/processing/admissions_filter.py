from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MIN_ADMISSIONS_RELEVANCE_SCORE = 3


@dataclass(slots=True)
class AdmissionsRelevance:
    score: int
    topics: list[str]
    drop_reason: str = ""


ADMISSIONS_TOPIC_RULES: dict[str, tuple[str, ...]] = {
    "college_essays": (
        "college essay",
        "college essays",
        "application essay",
        "application essays",
        "personal statement",
        "personal statements",
        "supplemental essay",
        "supplemental essays",
        "essay prompt",
        "why us essay",
    ),
    "common_app": (
        "common app",
        "common application",
        "additional information section",
    ),
    "activities_list": (
        "activities list",
        "activity list",
        "extracurricular activities",
        "extracurriculars",
        "honors section",
    ),
    "recommendation_letters": (
        "recommendation letter",
        "letters of recommendation",
        "teacher recommendation",
        "counselor recommendation",
        "rec letter",
    ),
    "financial_aid": (
        "financial aid",
        "fafsa",
        "css profile",
        "scholarship",
        "scholarships",
        "need-based aid",
        "merit aid",
        "net price",
    ),
    "early_decision": (
        "early decision",
        "early action",
        "regular decision",
        "restrictive early action",
        "ed1",
        "ed2",
        "rea",
    ),
    "application_timeline": (
        "application timeline",
        "college application timeline",
        "application deadline",
        "application deadlines",
        "application season",
        "senior year timeline",
    ),
    "college_interviews": (
        "college interview",
        "college interviews",
        "alumni interview",
        "admissions interview",
    ),
    "demonstrated_interest": (
        "demonstrated interest",
        "campus visit",
        "visit campus",
        "college visit",
        "information session",
    ),
    "college_list": (
        "college list",
        "school list",
        "reach school",
        "target school",
        "safety school",
        "balanced list",
        "college fit",
    ),
    "admissions_stats": (
        "acceptance rate",
        "admission rate",
        "admissions rate",
        "common data set",
        "college scorecard",
        "middle 50",
        "yield rate",
    ),
    "admissions_mistakes": (
        "admissions mistake",
        "application mistake",
        "common mistake",
        "mistakes to avoid",
        "red flag",
    ),
    "admissions_strategy": (
        "admissions strategy",
        "college admissions",
        "college admission",
        "college application",
        "applying to college",
        "admissions officer",
        "admissions officers",
        "admissions committee",
        "admissions reader",
        "admissions readers",
        "admissions process",
        "holistic admissions",
        "waitlist",
        "defer",
        "deferral",
    ),
    "access_and_transfer": (
        "first-generation applicant",
        "first-gen applicant",
        "low-income applicant",
        "transfer applicant",
        "transfer application",
        "transfer admissions",
        "international applicant",
        "questbridge",
        "underrepresented student",
    ),
}

ADMISSIONS_CONTEXT_TERMS = (
    "college admissions",
    "college admission",
    "college application",
    "college applicant",
    "college applicants",
    "apply to college",
    "applying to college",
    "application process",
    "admissions process",
    "admissions office",
    "admissions officer",
    "admissions readers",
    "admitted student",
)

LOW_RELEVANCE_RULES: dict[str, tuple[str, ...]] = {
    "dorm or campus life without an admissions angle": (
        "dorm",
        "dorm room",
        "roommate",
        "move-in",
        "dining hall",
        "campus food",
        "party",
        "parties",
        "nightlife",
        "fraternity",
        "sorority",
        "greek life",
    ),
    "general teen lifestyle without an admissions angle": (
        "fashion",
        "skincare",
        "dating",
        "prom",
        "teen lifestyle",
        "morning routine",
    ),
    "general career advice without an admissions angle": (
        "career advice",
        "job search",
        "job interview",
        "workplace",
        "salary negotiation",
    ),
    "generic productivity advice without an admissions angle": (
        "productivity",
        "time management",
        "study habits",
        "planner setup",
    ),
    "unrelated news without an admissions angle": (
        "election",
        "campaign",
        "school board",
        "legislature",
        "budget fight",
    ),
    "ranking content without application process value": (
        "college ranking",
        "college rankings",
        "best colleges",
        "top colleges",
    ),
    "promotional or navigational boilerplate": (
        "subscribe to our newsletter",
        "sign up for our newsletter",
        "buy now",
        "related articles",
        "advertisement",
        "cookie policy",
    ),
}


def evaluate_admissions_relevance(
    text: str,
    source_type: str = "",
    title: str = "",
    metadata: dict[str, Any] | None = None,
) -> AdmissionsRelevance:
    combined = " ".join(part for part in [title, text] if part).lower()
    topics = _matched_topics(combined)
    context_hits = sum(1 for term in ADMISSIONS_CONTEXT_TERMS if _contains(combined, term))
    low_relevance_reasons = _low_relevance_reasons(combined)

    if source_type == "official" and (
        _contains(combined, "admission rate") or _contains(combined, "college scorecard")
    ):
        topics.append("admissions_stats")

    topics = _unique(topics)
    has_direct_signal = bool(topics) or context_hits > 0
    if not has_direct_signal:
        return AdmissionsRelevance(
            score=1,
            topics=[],
            drop_reason=_drop_reason(low_relevance_reasons),
        )

    score = 3
    if len(topics) >= 2 or context_hits >= 2:
        score += 1
    if source_type == "official" or "admissions_stats" in topics:
        score += 1
    if low_relevance_reasons and len(topics) <= 1 and context_hits <= 1:
        score -= 1

    final_score = max(1, min(score, 5))
    return AdmissionsRelevance(
        score=final_score,
        topics=topics or ["admissions_strategy"],
        drop_reason="" if final_score >= MIN_ADMISSIONS_RELEVANCE_SCORE else _drop_reason(low_relevance_reasons),
    )


def _matched_topics(text: str) -> list[str]:
    return [
        topic
        for topic, keywords in ADMISSIONS_TOPIC_RULES.items()
        if any(_contains(text, keyword) for keyword in keywords)
    ]


def _low_relevance_reasons(text: str) -> list[str]:
    return [
        reason
        for reason, keywords in LOW_RELEVANCE_RULES.items()
        if any(_contains(text, keyword) for keyword in keywords)
    ]


def _drop_reason(low_relevance_reasons: list[str]) -> str:
    if low_relevance_reasons:
        return low_relevance_reasons[0]
    return "No direct college admissions process signal."


def _contains(text: str, keyword: str) -> bool:
    pattern = rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])"
    return re.search(pattern, text) is not None


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique_values.append(value)
    return unique_values
