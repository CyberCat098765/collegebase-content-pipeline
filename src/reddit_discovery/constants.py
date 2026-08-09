from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


REDDIT_CLIENT_ID_ENV = "REDDIT_CLIENT_ID"
REDDIT_CLIENT_SECRET_ENV = "REDDIT_CLIENT_SECRET"
REDDIT_USER_AGENT_ENV = "REDDIT_USER_AGENT"
REDDIT_ENV_VARS = (
    REDDIT_CLIENT_ID_ENV,
    REDDIT_CLIENT_SECRET_ENV,
    REDDIT_USER_AGENT_ENV,
)

SUPPORTED_SUBREDDIT = "ApplyingToCollege"
DEFAULT_MAX_COMMENTS_PER_POST = 15
DEFAULT_CANDIDATE_LIMIT = 500
DEFAULT_MINIMUM_USEFULNESS_SCORE = 70
DEFAULT_MAX_RETRIES = 3
PIPELINE_VERSION = "reddit-discovery-v3"
SCORING_VERSION = "reddit-usefulness-v3"
PROMPT_VERSION = "none"


SEARCH_QUERIES: dict[str, list[str]] = {
    "general_guides": [
        "guide",
        "resource",
        "megathread",
        "FAQ",
        "advice",
        "application tips",
        "things I wish I knew",
        "admissions guide",
    ],
    "college_list": [
        "college list",
        "balanced college list",
        "reach target safety",
        "safety schools",
        "choosing colleges",
    ],
    "common_app": [
        "Common App",
        "application timeline",
        "application checklist",
        "application process",
    ],
    "personal_essay": [
        "personal statement",
        "Common App essay",
        "college essay guide",
        "essay advice",
        "essay mistakes",
    ],
    "supplemental_essays": [
        "supplemental essays",
        "supplement guide",
        "why us essay",
        "community essay",
        "diversity essay",
    ],
    "activities": [
        "activities list",
        "extracurricular description",
        "Common App activities",
        "honors section",
        "additional information section",
    ],
    "recommendations": [
        "letter of recommendation",
        "recommendation letter",
        "teacher recommendation",
        "counselor recommendation",
        "brag sheet",
    ],
    "financial_aid": [
        "financial aid guide",
        "FAFSA",
        "CSS Profile",
        "net price calculator",
        "financial aid appeal",
        "need based aid",
        "merit aid",
    ],
    "scholarships": [
        "scholarship guide",
        "scholarships",
        "outside scholarships",
        "full ride",
        "merit scholarship",
    ],
    "application_rounds": [
        "early decision",
        "early action",
        "restrictive early action",
        "regular decision",
        "ED EA RD",
    ],
    "admissions_decisions": [
        "admissions decision guide",
        "understanding admissions decisions",
        "decision letter explained",
    ],
    "deferral_waitlist": [
        "deferred",
        "deferral guide",
        "waitlist guide",
        "letter of continued interest",
        "LOCI",
    ],
    "interviews": [
        "college interview",
        "alumni interview",
        "interview guide",
        "interview questions",
    ],
    "demonstrated_interest": [
        "demonstrated interest",
        "college visit",
        "admissions email",
    ],
    "choosing_a_college": [
        "compare college offers",
        "choosing between colleges",
        "cost versus fit",
        "enrollment decision",
    ],
    "application_logistics": [
        "application portal",
        "transcript submission",
        "application fee waiver",
        "midyear report",
    ],
    "testing": [
        "test optional",
        "SAT ACT admissions",
        "submit test scores",
        "standardized testing",
    ],
    "first_generation": [
        "first generation",
        "first gen applicant",
        "low income applicant",
        "QuestBridge",
    ],
    "international": [
        "international applicant guide",
        "international financial aid",
        "international admissions",
    ],
    "transfer": [
        "transfer application",
        "transfer admissions guide",
        "community college transfer",
    ],
    "institutional_knowledge": [
        "admissions officer AMA",
        "AO AMA",
        "verified admissions officer",
        "moderator guide",
    ],
}


TOPIC_TAXONOMY = (
    "general_application",
    "college_list",
    "common_app",
    "personal_essay",
    "supplemental_essays",
    "activities_and_honors",
    "recommendations",
    "financial_aid",
    "scholarships",
    "application_rounds",
    "admissions_decisions",
    "deferral_and_waitlist",
    "interviews",
    "demonstrated_interest",
    "choosing_a_college",
    "application_logistics",
    "standardized_testing",
    "first_generation",
    "international",
    "transfer",
    "admissions_officer_guidance",
    "other",
)

TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "general_application": (
        "admissions guide", "application process", "application timeline",
        "application checklist", "applying to college", "application advice",
        "college application", "college admissions", "admissions process",
        "application planning", "application cycle", "application retrospective",
    ),
    "college_list": (
        "college list", "balanced college list", "reach target safety",
        "safety school", "choosing colleges", "college fit",
    ),
    "common_app": (
        "common app", "common application", "additional information section",
    ),
    "personal_essay": (
        "personal statement", "common app essay", "college essay guide",
        "essay advice", "essay mistake", "application essay", "college essay",
    ),
    "supplemental_essays": (
        "supplemental essay", "supplement guide", "why us essay",
        "community essay", "diversity essay",
    ),
    "activities_and_honors": (
        "activities list", "extracurricular description", "common app activities",
        "honors section", "activity description", "activity descriptions",
        "extracurricular activities", "honors and awards",
    ),
    "recommendations": (
        "letter of recommendation", "recommendation letter", "teacher recommendation",
        "counselor recommendation", "brag sheet",
    ),
    "financial_aid": (
        "financial aid", "fafsa", "css profile", "net price calculator",
        "financial aid appeal", "need based aid", "merit aid",
    ),
    "scholarships": (
        "scholarship guide", "scholarships", "outside scholarship",
        "full ride", "merit scholarship",
    ),
    "application_rounds": (
        "early decision", "early action", "restrictive early action",
        "regular decision", "ed ea rd",
    ),
    "admissions_decisions": (
        "admissions decision", "decision letter", "decision release",
        "admission outcome", "acceptance decision",
    ),
    "deferral_and_waitlist": (
        "deferred", "deferral guide", "waitlist guide",
        "waitlist", "deferral", "letter of continued interest",
        "continued interest", "loci",
    ),
    "interviews": (
        "college interview", "alumni interview", "interview guide", "interview questions",
    ),
    "demonstrated_interest": (
        "demonstrated interest", "college visit", "admissions email",
    ),
    "choosing_a_college": (
        "compare college offers", "choosing between colleges", "choosing a college",
        "cost versus fit", "enrollment decision", "which college to attend",
    ),
    "application_logistics": (
        "application portal", "transcript submission", "application fee waiver",
        "fee waiver", "school report", "midyear report", "application logistics",
    ),
    "standardized_testing": (
        "test optional", "sat act admissions", "submit test scores", "standardized testing",
    ),
    "first_generation": (
        "first generation", "first gen applicant", "low income applicant", "questbridge",
    ),
    "international": (
        "international applicant", "international financial aid", "international admissions",
    ),
    "transfer": (
        "transfer application", "transfer admissions", "community college transfer",
    ),
    "admissions_officer_guidance": (
        "admissions officer ama", "ao ama", "verified admissions officer",
        "moderator guide", "admissions officer guidance",
    ),
}


@dataclass(frozen=True, slots=True)
class DiscoveryRoute:
    kind: Literal["listing", "search"]
    origin: str
    limit: int
    listing: str = ""
    time_filter: str | None = None
    query_group: str = ""
    query: str = ""
    sort: str = ""
    syntax: str = "lucene"


MAX_RANGE_LISTING_ROUTES = (
    DiscoveryRoute("listing", "top:all", 1000, listing="top", time_filter="all"),
    DiscoveryRoute("listing", "top:year", 1000, listing="top", time_filter="year"),
    DiscoveryRoute("listing", "top:month", 500, listing="top", time_filter="month"),
    DiscoveryRoute("listing", "top:week", 250, listing="top", time_filter="week"),
    DiscoveryRoute("listing", "new", 1000, listing="new"),
    DiscoveryRoute("listing", "hot", 250, listing="hot"),
    DiscoveryRoute("listing", "rising", 100, listing="rising"),
)

QUICK_LISTING_ROUTES = (
    DiscoveryRoute("listing", "top:year", 150, listing="top", time_filter="year"),
    DiscoveryRoute("listing", "hot", 75, listing="hot"),
    DiscoveryRoute("listing", "new", 100, listing="new"),
)

SEARCH_COMBINATIONS = (
    ("top", "all"),
    ("new", "year"),
)

QUICK_SEARCH_QUERIES = (
    ("general_guides", "guide"),
    ("personal_essay", "personal statement"),
    ("financial_aid", "financial aid guide"),
    ("common_app", "application timeline"),
)


def build_max_range_routes() -> tuple[DiscoveryRoute, ...]:
    return MAX_RANGE_LISTING_ROUTES + _search_routes(_all_search_queries())


def build_quick_routes() -> tuple[DiscoveryRoute, ...]:
    return QUICK_LISTING_ROUTES + _search_routes(QUICK_SEARCH_QUERIES)


def build_discovery_routes(*, max_range: bool, quick: bool) -> tuple[DiscoveryRoute, ...]:
    if max_range and quick:
        raise ValueError("--max-range and --quick are mutually exclusive.")
    if quick:
        return build_quick_routes()
    return build_max_range_routes()


def _all_search_queries() -> tuple[tuple[str, str], ...]:
    return tuple(
        (query_group, query)
        for query_group, queries in SEARCH_QUERIES.items()
        for query in queries
    )


def _search_routes(queries: tuple[tuple[str, str], ...]) -> tuple[DiscoveryRoute, ...]:
    return tuple(
        DiscoveryRoute(
            kind="search",
            origin=f"search:{query_group}:{query}:{sort}:{time_filter}",
            limit=250,
            query_group=query_group,
            query=query,
            sort=sort,
            time_filter=time_filter,
        )
        for query_group, query in queries
        for sort, time_filter in SEARCH_COMBINATIONS
    )


MAX_RANGE_ROUTES = build_max_range_routes()
QUICK_ROUTES = build_quick_routes()
