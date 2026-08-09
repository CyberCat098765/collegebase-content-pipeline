from __future__ import annotations

import importlib.util
from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path

from src.reddit_discovery.constants import (
    DEFAULT_CANDIDATE_LIMIT,
    DEFAULT_MAX_COMMENTS_PER_POST,
    DEFAULT_MINIMUM_USEFULNESS_SCORE,
    SUPPORTED_SUBREDDIT,
)
from src.reddit_discovery.provider_models import PROVIDER_CHOICES


@dataclass(frozen=True, slots=True)
class RedditDiscoveryOptions:
    subreddit: str = SUPPORTED_SUBREDDIT
    max_range: bool = False
    quick: bool = True
    input_path: Path | None = None
    provider: str = "auto"
    reddit_urls: tuple[str, ...] = ()
    include_comments: bool = False
    max_comments_per_post: int = DEFAULT_MAX_COMMENTS_PER_POST
    no_llm: bool = False
    force: bool = False
    resume: bool = False
    dry_run: bool = False
    validate_only: bool = False
    output_dir: Path = Path("outputs/reddit_applyingtocollege")
    candidate_limit: int | None = DEFAULT_CANDIDATE_LIMIT
    accepted_limit: int | None = None
    minimum_usefulness_score: int = DEFAULT_MINIMUM_USEFULNESS_SCORE
    verbose: bool = False


def options_from_namespace(args: Namespace) -> RedditDiscoveryOptions:
    return RedditDiscoveryOptions(
        subreddit=args.subreddit,
        max_range=bool(args.max_range),
        quick=bool(args.quick or (not args.max_range and not args.input_json)),
        input_path=Path(args.input_json) if args.input_json else None,
        provider=str(args.provider),
        reddit_urls=tuple(args.reddit_url or ()),
        include_comments=bool(args.include_comments and not args.no_comments),
        max_comments_per_post=args.max_comments_per_post,
        no_llm=bool(args.no_llm),
        force=bool(args.force),
        resume=bool(args.resume),
        dry_run=bool(args.dry_run),
        validate_only=bool(args.validate_only),
        output_dir=Path(args.output_dir),
        candidate_limit=args.candidate_limit,
        accepted_limit=args.accepted_limit,
        minimum_usefulness_score=args.minimum_usefulness_score,
        verbose=bool(args.verbose),
    )


def validate_options(options: RedditDiscoveryOptions) -> None:
    if options.provider not in PROVIDER_CHOICES:
        raise ValueError(f"provider must be one of: {', '.join(PROVIDER_CHOICES)}.")
    if options.max_comments_per_post <= 0:
        raise ValueError("max_comments_per_post must be positive.")
    if not 0 <= options.minimum_usefulness_score <= 100:
        raise ValueError("minimum_usefulness_score must be between 0 and 100.")
    for name, value in (
        ("candidate_limit", options.candidate_limit),
        ("accepted_limit", options.accepted_limit),
    ):
        if value is not None and value <= 0:
            raise ValueError(f"{name} must be positive when provided.")
    if options.input_path and options.include_comments:
        raise ValueError("--include-comments cannot be used with --input-json.")
    if options.input_path and options.resume:
        raise ValueError("--resume is only available for live Reddit acquisition.")
    if options.input_path and options.max_range:
        raise ValueError("--max-range cannot be used with --input-json.")
    if options.input_path and options.provider not in {"auto", "import"}:
        raise ValueError("--input-json can only be used with --provider auto or import.")
    if options.provider == "import" and options.input_path is None:
        raise ValueError("--provider import requires --input-json.")
    if options.provider == "manual" and not options.reddit_urls:
        raise ValueError("--provider manual requires at least one --reddit-url.")
    if options.reddit_urls and options.provider != "manual":
        raise ValueError("--reddit-url requires --provider manual.")
    if options.include_comments and options.provider not in {"auto", "praw"}:
        raise ValueError("--include-comments is only supported by the PRAW provider.")
    if options.resume and options.provider not in {"auto", "praw"}:
        raise ValueError("--resume is only supported by the PRAW provider.")


def validate_dependencies(*, require_praw: bool, require_http: bool = False) -> None:
    packages = ["rapidfuzz", "sklearn"]
    if require_praw:
        packages.append("praw")
    if require_http:
        packages.extend(("requests", "bs4"))
    for package in packages:
        if importlib.util.find_spec(package) is None:
            raise ValueError(f"Required dependency is not installed: {package}")
