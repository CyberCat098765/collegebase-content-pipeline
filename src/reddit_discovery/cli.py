from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Sequence

from src.reddit_discovery.auth import (
    INVALID_CREDENTIALS_NEXT_STEP,
    MISSING_CREDENTIALS_NEXT_STEP,
    MissingRedditCredentials,
    RedditAuthError,
    create_reddit_client,
)
from src.reddit_discovery.auth_retry import (
    probe_reddit_auth_with_retries as probe_reddit_auth,
)
from src.reddit_discovery.constants import (
    DEFAULT_CANDIDATE_LIMIT,
    DEFAULT_MAX_COMMENTS_PER_POST,
    DEFAULT_MINIMUM_USEFULNESS_SCORE,
    SUPPORTED_SUBREDDIT,
)
from src.reddit_discovery.provider_models import PROVIDER_CHOICES


def build_discovery_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Discover reusable CollegeBase resources from r/ApplyingToCollege "
            "through bounded authorized, Atom, curated, or offline providers."
        )
    )
    parser.add_argument(
        "--subreddit",
        default=SUPPORTED_SUBREDDIT,
        help="Subreddit to query (only ApplyingToCollege is supported).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--max-range",
        action="store_true",
        help="Use all configured official listing and search routes.",
    )
    mode.add_argument(
        "--quick",
        action="store_true",
        help="Use the bounded quick route set (the safe live default).",
    )
    mode.add_argument(
        "--input-json",
        metavar="PATH",
        help="Process normalized Reddit candidates from a local .json or .jsonl file.",
    )
    parser.add_argument(
        "--provider",
        choices=PROVIDER_CHOICES,
        default="auto",
        help=(
            "Acquisition provider. Auto uses PRAW when configured; otherwise it uses "
            "curated A2C and bounded Atom feeds for development/calibration."
        ),
    )
    parser.add_argument(
        "--reddit-url",
        action="append",
        default=[],
        metavar="URL",
        help="Manual r/ApplyingToCollege post URL; repeat with --provider manual.",
    )
    comments = parser.add_mutually_exclusive_group()
    comments.add_argument(
        "--include-comments",
        action="store_true",
        help="Fetch bounded top-level comments after deterministic filtering.",
    )
    comments.add_argument(
        "--no-comments",
        action="store_true",
        help="Do not fetch comments (the default).",
    )
    parser.add_argument(
        "--max-comments-per-post",
        type=_positive_int,
        default=DEFAULT_MAX_COMMENTS_PER_POST,
        metavar="N",
        help=f"Maximum useful top-level comments per post (default: {DEFAULT_MAX_COMMENTS_PER_POST}).",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Use deterministic heuristic classification only.",
    )
    parser.add_argument("--force", action="store_true", help="Reprocess all discovered posts.")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume the latest compatible route checkpoint.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the discovery plan without API calls or writes.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate options, route registry, and dependencies without writes.",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/reddit_applyingtocollege",
        help="Directory for the Reddit output bundle.",
    )
    parser.add_argument(
        "--candidate-limit",
        type=_positive_int,
        default=DEFAULT_CANDIDATE_LIMIT,
        metavar="N",
        help=f"Maximum unique candidates to acquire (default: {DEFAULT_CANDIDATE_LIMIT}).",
    )
    parser.add_argument("--accepted-limit", type=_positive_int, metavar="N")
    parser.add_argument(
        "--minimum-usefulness-score",
        type=_score,
        default=DEFAULT_MINIMUM_USEFULNESS_SCORE,
        metavar="0-100",
        help=f"Acceptance threshold (default: {DEFAULT_MINIMUM_USEFULNESS_SCORE}).",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable detailed progress logging.")
    return parser


def discovery_main(argv: Sequence[str] | None = None) -> int:
    parser = build_discovery_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    try:
        from src.reddit_discovery.options import options_from_namespace
        from src.reddit_discovery.pipeline import run_reddit_discovery

        result = run_reddit_discovery(options_from_namespace(args))
    except (MissingRedditCredentials, RedditAuthError, ValueError, OSError) as exc:
        print(f"Reddit discovery failed: {exc}", file=sys.stderr)
        if isinstance(exc, MissingRedditCredentials):
            print(f"Next step: {MISSING_CREDENTIALS_NEXT_STEP}", file=sys.stderr)
        return 2

    summary = result.summary
    if args.dry_run or args.validate_only:
        label = "Validation" if args.validate_only else "Dry run"
        print(f"{label} passed")
        print(f"- Subreddit: r/{summary['subreddit']}")
        print(f"- Mode: {summary['mode']}")
        print(f"- Provider: {summary.get('provider', 'unknown')}")
        if summary.get("provider_plan"):
            print(f"- Provider plan: {', '.join(summary['provider_plan'])}")
        print(f"- Discovery routes: {summary['route_count']}")
        if summary.get("input_path"):
            print(f"- Imported candidates: {summary.get('imported_candidate_count', 0)}")
            print(f"- Import errors: {summary.get('import_error_count', 0)}")
        print("- Persistent writes: 0")
        return 0

    print("Reddit discovery completed")
    print(f"- Output directory: {Path(args.output_dir)}")
    print(f"- Candidates: {summary.get('candidate_count', 0)}")
    print(f"- Acquired this run: {summary.get('candidates_acquired_this_run', 0)}")
    print(f"- Provider: {summary.get('provider', 'unknown')}")
    print(f"- HTTP requests: {summary.get('http_request_count', 0)}")
    print(f"- Accepted: {summary.get('accepted_count', 0)}")
    print(f"- Human review: {summary.get('human_review_count', 0)}")
    print(f"- Rejected: {summary.get('rejected_count', 0)}")
    print(f"- Duplicate clusters: {summary.get('duplicate_cluster_count', 0)}")
    print(
        "- Processing cache hits/misses: "
        f"{summary.get('cache_hit_count', 0)}/{summary.get('cache_miss_count', 0)}"
    )
    print(
        "- HTTP cache hits/misses: "
        f"{summary.get('http_cache_hit_count', 0)}/"
        f"{summary.get('http_cache_miss_count', 0)}"
    )
    print(f"- Errors: {summary.get('error_count', 0)}")
    print(f"- Stop reason: {summary.get('stop_reason', 'completed')}")
    return 0


def capability_check_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a bounded capability check for CollegeBase Reddit providers."
    )
    parser.parse_args(argv)
    from src.reddit_discovery.capabilities import check_reddit_capabilities

    results = check_reddit_capabilities()
    print("Reddit provider capability check")
    print("provider\tstatus\tcode\trequests\tuseful data")
    for result in results:
        code = str(result.status_code) if result.status_code is not None else "-"
        print(
            f"{result.provider}\t{result.status}\t{code}\t"
            f"{result.request_count}\t{result.useful_data}"
        )
        if result.limitations:
            print(f"  limitation: {result.limitations}")
    print(
        "Production note: technical access does not grant commercial or production "
        "authorization; use approved Reddit access for deployment."
    )
    return 0


def auth_check_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check read-only Reddit OAuth access for r/ApplyingToCollege."
    )
    parser.parse_args(argv)

    try:
        reddit = create_reddit_client(load_env=True)
    except MissingRedditCredentials as exc:
        _print_auth_failure(str(exc), MISSING_CREDENTIALS_NEXT_STEP)
        return 2
    except RedditAuthError as exc:
        _print_auth_failure(str(exc), INVALID_CREDENTIALS_NEXT_STEP)
        return 3

    result = probe_reddit_auth(reddit, SUPPORTED_SUBREDDIT)
    success = result.success and result.accessible and result.read_only
    print(f"Authentication: {'success' if success else 'failure'}")
    print(f"Read-only: {str(result.read_only).lower()}")
    print(f"Subreddit name: {result.subreddit or SUPPORTED_SUBREDDIT}")
    print(f"Subreddit title: {result.subreddit_title or 'unavailable'}")
    print(f"Subreddit accessible: {str(result.accessible).lower()}")
    print("Rate-limit information: " + json.dumps(result.rate_limit, sort_keys=True))
    if not success:
        error = result.error or "The Reddit client is not confirmed read-only."
        print(f"Error: {error}")
        print(f"Next step: {result.next_step or INVALID_CREDENTIALS_NEXT_STEP}")
        return 4
    return 0


def _print_auth_failure(error: str, next_step: str) -> None:
    print("Authentication: failure")
    print("Read-only: unavailable")
    print(f"Subreddit name: {SUPPORTED_SUBREDDIT}")
    print("Subreddit title: unavailable")
    print("Subreddit accessible: false")
    print("Rate-limit information: {}")
    print(f"Error: {error}")
    print(f"Next step: {next_step}")


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _score(value: str) -> int:
    parsed = int(value)
    if not 0 <= parsed <= 100:
        raise argparse.ArgumentTypeError("must be between 0 and 100")
    return parsed


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
