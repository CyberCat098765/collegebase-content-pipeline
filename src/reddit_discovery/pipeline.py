from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.reddit_discovery.auth import (
    RedditAuthError,
    create_reddit_client,
    reddit_rate_limits,
)
from src.reddit_discovery.auth_retry import (
    probe_reddit_auth_with_retries as probe_reddit_auth,
)
from src.reddit_discovery.acquisition_runner import acquire_candidate_batch
from src.reddit_discovery.candidate_import import load_candidate_file
from src.reddit_discovery.constants import (
    PIPELINE_VERSION,
    PROMPT_VERSION,
    build_discovery_routes,
)
from src.reddit_discovery.dedupe import DedupeResult, deduplicate_candidates, ensure_resource_id
from src.reddit_discovery.discovery import (
    collect_comments_for_candidates,
    validate_subreddit_name,
)
from src.reddit_discovery.filtering import evaluate_hard_filters
from src.reddit_discovery.models import DiscoveryError, RedditCandidate
from src.reddit_discovery.outputs import (
    OutputBundle,
    build_output_bundle,
    build_run_summary,
    output_paths,
    validate_output_bundle,
    write_output_bundle,
)
from src.reddit_discovery.options import (
    RedditDiscoveryOptions,
    validate_dependencies,
    validate_options,
)
from src.reddit_discovery.providers import (
    free_provider_plan,
    resolve_provider,
    stop_reason,
)
from src.reddit_discovery.registry import SourceRegistry
from src.reddit_discovery.run_support import (
    apply_accepted_limit,
    archive_existing_outputs,
    classify_retained,
    complete_checkpoint,
    exact_duplicate_clusters,
    mark_not_scored,
    rank_candidates,
    record_candidate,
    refresh_carried_metadata,
    registry_candidates,
    registry_pipeline_version,
    registry_scoring_version,
    unique_candidates,
)
from src.reddit_discovery.scoring import apply_heuristic_evaluation
from src.reddit_discovery.retry import should_abort_after_reddit_error
from src.time_utils import utc_now


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RedditDiscoveryRunResult:
    summary: dict[str, Any]
    bundle: OutputBundle | None = None
    written_paths: tuple[Path, ...] = field(default_factory=tuple)


def run_reddit_discovery(options: RedditDiscoveryOptions) -> RedditDiscoveryRunResult:
    subreddit = validate_subreddit_name(options.subreddit)
    offline_import = options.input_path is not None
    provider = resolve_provider(options)
    praw_provider = provider == "praw"
    routes = (
        ()
        if not praw_provider
        else build_discovery_routes(max_range=options.max_range, quick=options.quick)
    )
    validate_options(options)
    if options.include_comments and not praw_provider:
        raise ValueError("--include-comments requires authorized PRAW access in this run.")
    if options.resume and not praw_provider:
        raise ValueError("--resume requires authorized PRAW access in this run.")
    mode = "offline-import" if offline_import else "quick" if options.quick else "max-range"
    provider_plan = (
        ("import",)
        if offline_import
        else ("praw",)
        if praw_provider
        else free_provider_plan(provider)
    )
    plan_summary = {
        "status": "validated",
        "subreddit": subreddit,
        "mode": mode,
        "route_count": len(routes) if praw_provider else len(provider_plan),
        "candidate_limit": options.candidate_limit,
        "accepted_limit": options.accepted_limit,
        "include_comments": options.include_comments,
        "maximum_comments_per_post": options.max_comments_per_post,
        "minimum_usefulness_score": options.minimum_usefulness_score,
        "llm_status": "disabled_by_flag" if options.no_llm else "not_configured",
        "input_path": options.input_path.name if options.input_path else "",
        "provider": provider,
        "provider_plan": list(provider_plan),
        "acquisition_method": (
            "imported_json"
            if offline_import
            else "reddit_api"
            if praw_provider
            else "free_development_providers"
        ),
    }
    logger.info("Reddit discovery starting for r/%s in %s mode.", subreddit, mode)
    if options.dry_run or options.validate_only:
        validate_dependencies(
            require_praw=praw_provider,
            require_http=not offline_import and not praw_provider,
        )
        if options.input_path:
            imported = load_candidate_file(options.input_path)
            plan_summary.update(
                {
                    "imported_candidate_count": len(imported.candidates),
                    "import_error_count": len(imported.errors),
                }
            )
        return RedditDiscoveryRunResult(plan_summary)

    validate_dependencies(
        require_praw=praw_provider,
        require_http=not offline_import and not praw_provider,
    )
    started_at = utc_now()
    started_monotonic = time.monotonic()
    reddit: Any | None = None
    imported = (
        load_candidate_file(options.input_path, retrieved_at=started_at)
        if options.input_path
        else None
    )
    if praw_provider:
        reddit = create_reddit_client(load_env=True)
        auth_probe = probe_reddit_auth(reddit, subreddit)
        if not auth_probe.success or not auth_probe.accessible or not auth_probe.read_only:
            detail = auth_probe.error or "Reddit read-only access could not be confirmed."
            raise RedditAuthError(f"{detail} Next step: {auth_probe.next_step}")

    archive_existing_outputs(options.output_dir, started_at)
    paths = output_paths(options.output_dir)
    registry = SourceRegistry.load(paths["source_registry"])
    acquisition = acquire_candidate_batch(
        options,
        subreddit=subreddit,
        routes=routes,
        started_at=started_at,
        reddit=reddit,
        imported=imported,
    )
    discovery = acquisition.discovery
    provider_statuses = acquisition.provider_statuses
    checkpoint = acquisition.checkpoint
    checkpoint_path = acquisition.checkpoint_path
    fingerprint = acquisition.fingerprint
    http_request_count = acquisition.http_request_count
    http_cache_hits = acquisition.http_cache_hits
    http_cache_misses = acquisition.http_cache_misses

    logger.info(
        "Reddit acquisition completed with %d candidates, %d duplicates, and %d errors.",
        len(discovery.candidates),
        len(discovery.duplicates),
        len(discovery.errors),
    )

    processed_ids: set[str] = set()
    viable: list[RedditCandidate] = []
    hard_rejected: list[RedditCandidate] = []
    processing_errors: list[DiscoveryError] = []
    skipped_unchanged = 0
    reprocessed_changed = 0
    registry_version = registry_pipeline_version(options)

    for candidate in discovery.candidates:
        decision = registry.observe(
            candidate,
            seen_at=candidate.retrieved_at,
            pipeline_version=registry_version,
            prompt_version=PROMPT_VERSION,
            scoring_version=registry_scoring_version(options),
            force=options.force,
        )
        if decision.should_skip:
            carried = RedditCandidate.from_dict(decision.carried_record) if decision.carried_record else None
            if carried is not None:
                carried.merge_discovered_by(candidate.discovered_by)
                refresh_carried_metadata(carried, candidate)
            if carried is not None and carried.duplicate_of:
                carried.duplicate_of = None
                carried.rejection_reason = None
                carried.hard_rejection_reason = None
                carried.processing_status = "discovered"
                carried.requires_human_review = False
                candidate = carried
            else:
                skipped_unchanged += 1
                if carried is not None:
                    registry.posts[candidate.reddit_post_id]["record_data"] = carried.to_dict()
                continue
        processed_ids.add(candidate.reddit_post_id)
        if decision.reason == "content_changed":
            reprocessed_changed += 1
        try:
            filter_result = evaluate_hard_filters(candidate)
            if filter_result.rejected:
                mark_not_scored(candidate, filter_result.reason_code or "HARD_REJECTED")
                hard_rejected.append(candidate)
                continue
            apply_heuristic_evaluation(candidate)
            viable.append(candidate)
        except Exception as exc:
            mark_not_scored(candidate, "PROCESSING_ERROR")
            hard_rejected.append(candidate)
            processing_errors.append(
                DiscoveryError(
                    route=f"processing:{candidate.reddit_post_id}",
                    error_type=type(exc).__name__,
                    message=f"Candidate processing failed: {type(exc).__name__}.",
                    retryable=False,
                    attempts=1,
                )
            )

    exact_rejected: list[RedditCandidate] = []
    for candidate in discovery.duplicates:
        ensure_resource_id(candidate)
        if candidate.duplicate_of and not candidate.duplicate_of.startswith("reddit_"):
            candidate.duplicate_of = f"reddit_{candidate.duplicate_of}"
        mark_not_scored(candidate, candidate.rejection_reason or "DUPLICATE_CANONICAL_URL")
        decision = registry.observe(
            candidate,
            seen_at=candidate.retrieved_at,
            pipeline_version=registry_version,
            prompt_version=PROMPT_VERSION,
            scoring_version=registry_scoring_version(options),
            force=options.force,
        )
        if decision.should_skip:
            skipped_unchanged += 1
            carried = (
                RedditCandidate.from_dict(decision.carried_record)
                if decision.carried_record
                else candidate
            )
            carried.merge_discovered_by(candidate.discovered_by)
            exact_rejected.append(carried)
            continue
        exact_rejected.append(candidate)
        processed_ids.add(candidate.reddit_post_id)

    carried_accepted = [
        candidate
        for candidate in registry_candidates(registry, "accepted")
        if candidate.reddit_post_id not in processed_ids
    ]
    carried_review = [
        candidate
        for candidate in registry_candidates(registry, "human_review")
        if candidate.reddit_post_id not in processed_ids
    ]
    dedupe_input = [*carried_accepted, *carried_review, *viable]
    try:
        dedupe_result = deduplicate_candidates(dedupe_input)
    except Exception as exc:
        dedupe_result = DedupeResult(retained=dedupe_input, duplicates=[], clusters=[])
        processing_errors.append(
            DiscoveryError(
                route="deduplication",
                error_type=type(exc).__name__,
                message=f"Near-duplicate detection failed: {type(exc).__name__}.",
                retryable=False,
                attempts=1,
            )
        )
    comment_errors: list[Any] = []
    comment_candidates = rank_candidates(
        candidate
        for candidate in dedupe_result.retained
        if candidate.reddit_post_id in processed_ids
    )
    if options.include_comments and comment_candidates:
        global_error = next(
            (
                error
                for error in discovery.errors
                if should_abort_after_reddit_error(error)
            ),
            None,
        )
        if global_error is not None:
            comment_errors = [
                DiscoveryError(
                    route=f"comments:{candidate.reddit_post_id}",
                    error_type=global_error.error_type,
                    message=(
                        "Comment collection skipped after an exhausted global "
                        f"Reddit API failure: {global_error.error_type}."
                    ),
                    retryable=global_error.retryable,
                    attempts=0,
                    status_code=global_error.status_code,
                )
                for candidate in comment_candidates
            ]
        else:
            comment_errors = collect_comments_for_candidates(
                reddit,
                comment_candidates,
                max_comments_per_post=options.max_comments_per_post,
            )
    accepted, review, score_rejected = classify_retained(
        dedupe_result,
        registry,
        processed_ids,
        options.minimum_usefulness_score,
    )
    near_rejected = dedupe_result.rejected_candidates
    processed_ids.update(candidate.reddit_post_id for candidate in near_rejected)

    if options.accepted_limit is not None and len(accepted) > options.accepted_limit:
        accepted, overflow = apply_accepted_limit(accepted, options.accepted_limit)
        review.extend(overflow)
        processed_ids.update(candidate.reddit_post_id for candidate in overflow)

    carried_rejected = [
        candidate
        for candidate in registry_candidates(registry, "rejected")
        if candidate.reddit_post_id not in processed_ids
    ]
    rejected = unique_candidates(
        [*carried_rejected, *hard_rejected, *exact_rejected, *score_rejected, *near_rejected]
    )
    accepted = rank_candidates(unique_candidates(accepted))
    review = rank_candidates(unique_candidates(review))
    comment_failed_ids = {
        str(getattr(error, "route", "")).removeprefix("comments:")
        for error in comment_errors
        if str(getattr(error, "route", "")).startswith("comments:")
    }

    for status, candidates in (
        ("accepted", accepted),
        ("human_review", review),
        ("rejected", rejected),
    ):
        for candidate in candidates:
            if candidate.reddit_post_id not in processed_ids:
                continue
            record_candidate(
                registry,
                candidate,
                status,
                incomplete_reason=(
                    "COMMENT_FETCH_FAILED"
                    if candidate.reddit_post_id in comment_failed_ids
                    else None
                ),
            )

    raw_candidates = unique_candidates(
        [
            *discovery.candidates,
            *discovery.duplicates,
            *registry_candidates(registry),
        ]
    )
    cluster_representatives = [
        candidate
        for candidate in [*accepted, *review, *rejected]
        if not candidate.duplicate_of
    ]
    clusters = [
        *exact_duplicate_clusters(exact_rejected, cluster_representatives),
        *(cluster.to_dict() for cluster in dedupe_result.clusters),
    ]
    completed_at = utc_now()
    checkpoint_errors = checkpoint.load_errors if checkpoint is not None else []
    errors: list[Any] = [
        *registry.load_errors,
        *checkpoint_errors,
        *discovery.errors,
        *comment_errors,
        *processing_errors,
    ]
    summary = build_run_summary(
        raw_candidates=[candidate.to_dict() for candidate in raw_candidates],
        accepted_resources=[candidate.to_dict() for candidate in accepted],
        human_review=[candidate.to_dict() for candidate in review],
        rejected_candidates=[candidate.to_dict() for candidate in rejected],
        duplicate_clusters=clusters,
        errors=errors,
        route_counts=discovery.route_counts,
        started_at=started_at,
        completed_at=completed_at,
        duration_seconds=round(time.monotonic() - started_monotonic, 3),
        subreddit=subreddit,
        mode=mode,
        skipped_unchanged_count=skipped_unchanged,
        reprocessed_changed_count=reprocessed_changed,
        extra={
            **plan_summary,
            "status": "completed_with_errors" if errors else "completed",
            "completed_route_count": len(discovery.completed_routes),
            "candidate_limit_reached": discovery.limit_reached,
            "candidates_acquired_this_run": (
                len(discovery.candidates) + len(discovery.duplicates)
            ),
            "import_error_count": len(discovery.errors) if offline_import else 0,
            "cache_miss_count": len(processed_ids),
            "http_cache_hit_count": http_cache_hits,
            "http_cache_miss_count": http_cache_misses,
            "http_request_count": http_request_count,
            "provider_statuses": provider_statuses,
            "stop_reason": stop_reason(
                discovery_limit_reached=discovery.limit_reached,
                provider_statuses=provider_statuses,
            ),
            "rate_limit": reddit_rate_limits(reddit) if reddit is not None else {},
            "pipeline_version": PIPELINE_VERSION,
            "model_name": "",
            "prompt_version": PROMPT_VERSION,
        },
    )
    bundle = build_output_bundle(
        raw_candidates=raw_candidates,
        accepted_resources=accepted,
        human_review=review,
        rejected_candidates=rejected,
        duplicate_clusters=clusters,
        source_registry=registry.to_dict(),
        run_summary=summary,
        pipeline_version=PIPELINE_VERSION,
    )
    validation_errors = validate_output_bundle(bundle)
    if validation_errors:
        raise ValueError("Output validation failed: " + "; ".join(validation_errors))
    written = write_output_bundle(bundle, options.output_dir)
    if checkpoint is not None and checkpoint_path is not None:
        expected_routes = {route.origin for route in routes}
        complete_checkpoint(
            checkpoint,
            checkpoint_path,
            fingerprint,
            discovery,
            all_routes_completed=(
                discovery.limit_reached
                or expected_routes.issubset(discovery.completed_routes)
            ),
        )
    logger.info(
        "Reddit discovery finished: %d accepted, %d review, %d rejected, %d errors.",
        len(accepted),
        len(review),
        len(rejected),
        len(errors),
    )
    return RedditDiscoveryRunResult(summary, bundle, written)
