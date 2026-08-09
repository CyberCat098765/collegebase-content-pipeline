from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.reddit_discovery.candidate_import import CandidateImportResult
from src.reddit_discovery.checkpoint import DiscoveryCheckpoint, load_checkpoint
from src.reddit_discovery.constants import DiscoveryRoute
from src.reddit_discovery.discovery import discover_candidates
from src.reddit_discovery.models import DiscoveryResult
from src.reddit_discovery.options import RedditDiscoveryOptions
from src.reddit_discovery.providers import acquire_free_candidates
from src.reddit_discovery.run_support import (
    checkpoint_is_interrupted,
    checkpoint_seed,
    run_fingerprint,
    save_route_checkpoint,
)


CHECKPOINT_FILENAME = ".reddit_discovery_checkpoint.json"


@dataclass(slots=True)
class AcquisitionExecution:
    discovery: DiscoveryResult
    provider_statuses: list[dict[str, Any]] = field(default_factory=list)
    checkpoint: DiscoveryCheckpoint | None = None
    checkpoint_path: Path | None = None
    fingerprint: str = ""
    http_request_count: int = 0
    http_cache_hits: int = 0
    http_cache_misses: int = 0


def acquire_candidate_batch(
    options: RedditDiscoveryOptions,
    *,
    subreddit: str,
    routes: tuple[DiscoveryRoute, ...],
    started_at: str,
    reddit: Any | None,
    imported: CandidateImportResult | None,
) -> AcquisitionExecution:
    if options.input_path is not None:
        assert imported is not None
        import_route = f"import:{options.input_path.name}"
        discovery = DiscoveryResult(
            candidates=imported.candidates,
            errors=imported.errors,
            route_counts={import_route: len(imported.candidates)},
            completed_routes=[import_route],
        )
        return AcquisitionExecution(
            discovery=discovery,
            provider_statuses=[_import_status(imported)],
        )

    if reddit is not None:
        return _acquire_praw(options, subreddit, routes, reddit)

    acquisition = acquire_free_candidates(options, retrieved_at=started_at)
    statuses = [status.to_dict() for status in acquisition.statuses]
    if options.provider == "auto":
        statuses.insert(0, _missing_praw_status())
    return AcquisitionExecution(
        discovery=acquisition.discovery,
        provider_statuses=statuses,
        http_request_count=acquisition.request_count,
        http_cache_hits=acquisition.cache_hit_count,
        http_cache_misses=acquisition.cache_miss_count,
    )


def _acquire_praw(
    options: RedditDiscoveryOptions,
    subreddit: str,
    routes: tuple[DiscoveryRoute, ...],
    reddit: Any,
) -> AcquisitionExecution:
    fingerprint = run_fingerprint(options, routes)
    checkpoint_path = options.output_dir / CHECKPOINT_FILENAME
    checkpoint = (
        load_checkpoint(checkpoint_path, expected_run_fingerprint=fingerprint)
        if options.resume
        else DiscoveryCheckpoint(fingerprint)
    )
    if not (options.resume and checkpoint_is_interrupted(checkpoint, fingerprint)):
        checkpoint = DiscoveryCheckpoint(
            fingerprint,
            load_errors=list(checkpoint.load_errors),
        )
    seed, completed_routes, restored_route_counts = checkpoint_seed(
        checkpoint,
        fingerprint,
    )

    def checkpoint_route(result: DiscoveryResult, route: DiscoveryRoute) -> None:
        save_route_checkpoint(
            checkpoint,
            checkpoint_path,
            fingerprint,
            result,
            route,
        )

    discovery = discover_candidates(
        reddit,
        subreddit_name=subreddit,
        max_range=options.max_range,
        quick=options.quick,
        routes=routes,
        candidate_limit=options.candidate_limit,
        seed_candidates=seed,
        completed_routes=completed_routes,
        checkpoint_callback=checkpoint_route,
    )
    discovery.route_counts = {**restored_route_counts, **discovery.route_counts}
    return AcquisitionExecution(
        discovery=discovery,
        provider_statuses=[_praw_status(discovery)],
        checkpoint=checkpoint,
        checkpoint_path=checkpoint_path,
        fingerprint=fingerprint,
    )


def _import_status(imported: CandidateImportResult) -> dict[str, Any]:
    return {
        "provider": "import",
        "status": "completed" if imported.candidates else "completed_empty",
        "candidate_count": len(imported.candidates),
        "request_count": 0,
        "cache_hit_count": 0,
        "cache_miss_count": 0,
        "status_code": None,
        "content_type": "",
        "error": imported.errors[0].message if imported.errors else "",
        "intended_use": "offline testing/fallback",
    }


def _praw_status(discovery: DiscoveryResult) -> dict[str, Any]:
    return {
        "provider": "praw",
        "status": "completed_with_errors" if discovery.errors else "completed",
        "candidate_count": len(discovery.candidates),
        "request_count": None,
        "cache_hit_count": 0,
        "cache_miss_count": 0,
        "status_code": discovery.errors[0].status_code if discovery.errors else None,
        "content_type": "application/json",
        "error": discovery.errors[0].message if discovery.errors else "",
        "intended_use": "authorized production candidate",
    }


def _missing_praw_status() -> dict[str, Any]:
    return {
        "provider": "praw",
        "status": "skipped_missing_credentials",
        "candidate_count": 0,
        "request_count": 0,
        "cache_hit_count": 0,
        "cache_miss_count": 0,
        "status_code": None,
        "content_type": "",
        "error": "Authorized Reddit credentials are not configured.",
        "intended_use": "authorized production candidate",
    }
