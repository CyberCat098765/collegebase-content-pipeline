"""Focused Reddit resource discovery for CollegeBase."""

from src.reddit_discovery.auth import (
    AuthProbeResult,
    MissingRedditCredentials,
    RedditAuthError,
    RedditCredentials,
    create_reddit_client,
    credentials_from_environment,
    load_reddit_environment,
    probe_reddit_auth,
)
from src.reddit_discovery.constants import (
    QUICK_ROUTES,
    SEARCH_QUERIES,
    TOPIC_TAXONOMY,
    MAX_RANGE_ROUTES,
    DiscoveryRoute,
    build_discovery_routes,
)
from src.reddit_discovery.candidate_import import CandidateImportResult, load_candidate_file
from src.reddit_discovery.discovery import (
    candidate_from_submission,
    collect_comments_for_candidates,
    discover_candidates,
    merge_candidates_by_id_and_url,
)
from src.reddit_discovery.models import (
    DiscoveryError,
    DiscoveryResult,
    RedditCandidate,
    RedditComment,
)
from src.reddit_discovery.options import RedditDiscoveryOptions

__all__ = [
    "AuthProbeResult",
    "CandidateImportResult",
    "DiscoveryError",
    "DiscoveryResult",
    "DiscoveryRoute",
    "MAX_RANGE_ROUTES",
    "MissingRedditCredentials",
    "QUICK_ROUTES",
    "RedditAuthError",
    "RedditCandidate",
    "RedditComment",
    "RedditCredentials",
    "RedditDiscoveryOptions",
    "SEARCH_QUERIES",
    "TOPIC_TAXONOMY",
    "build_discovery_routes",
    "candidate_from_submission",
    "collect_comments_for_candidates",
    "create_reddit_client",
    "credentials_from_environment",
    "discover_candidates",
    "load_reddit_environment",
    "load_candidate_file",
    "merge_candidates_by_id_and_url",
    "probe_reddit_auth",
]
