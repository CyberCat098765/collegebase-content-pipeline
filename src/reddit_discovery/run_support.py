from __future__ import annotations

import hashlib
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from src.reddit_discovery.checkpoint import (
    DiscoveryCheckpoint,
    save_checkpoint,
    stable_fingerprint,
)
from src.reddit_discovery.constants import PIPELINE_VERSION, SCORING_VERSION, DiscoveryRoute
from src.reddit_discovery.dedupe import DedupeResult, ensure_resource_id
from src.reddit_discovery.models import DiscoveryResult, RedditCandidate
from src.reddit_discovery.outputs import output_paths
from src.reddit_discovery.registry import SourceRegistry
from src.reddit_discovery.scoring import classify_candidate
from src.time_utils import utc_now


def run_fingerprint(options: Any, routes: Iterable[DiscoveryRoute]) -> str:
    return stable_fingerprint(
        {
            "subreddit": "ApplyingToCollege",
            "mode": "quick" if options.quick else "max-range",
            "candidate_limit": options.candidate_limit,
            "routes": [asdict(route) for route in routes],
            "pipeline_version": PIPELINE_VERSION,
        }
    )


def checkpoint_seed(
    checkpoint: DiscoveryCheckpoint, fingerprint: str
) -> tuple[list[RedditCandidate], list[str], dict[str, int]]:
    aggregate = checkpoint.get_route("__aggregate__", route_fingerprint=fingerprint)
    state = aggregate.get("state", {}) if aggregate else {}
    candidates = state.get("candidates", []) if isinstance(state, dict) else []
    duplicates = state.get("duplicates", []) if isinstance(state, dict) else []
    seed = [
        RedditCandidate.from_dict(item)
        for item in [*candidates, *duplicates]
        if isinstance(item, dict)
    ]
    completed = [
        route_id
        for route_id, route in checkpoint.routes.items()
        if route_id != "__aggregate__" and route.get("status") == "completed"
    ]
    counts = state.get("route_counts", {}) if isinstance(state, dict) else {}
    route_counts = {
        str(route): int(count)
        for route, count in counts.items()
        if isinstance(count, int) and not isinstance(count, bool)
    } if isinstance(counts, dict) else {}
    for route_id in completed:
        route = checkpoint.routes.get(route_id, {})
        route_state = route.get("state", {})
        count = route_state.get("candidate_count") if isinstance(route_state, dict) else None
        if isinstance(count, int) and not isinstance(count, bool):
            route_counts.setdefault(route_id, count)
    return seed, completed, route_counts


def checkpoint_is_interrupted(
    checkpoint: DiscoveryCheckpoint, fingerprint: str
) -> bool:
    aggregate = checkpoint.get_route("__aggregate__", route_fingerprint=fingerprint)
    return bool(aggregate and aggregate.get("status") != "completed")


def save_route_checkpoint(
    checkpoint: DiscoveryCheckpoint,
    path: Path,
    fingerprint: str,
    result: DiscoveryResult,
    route: DiscoveryRoute,
) -> None:
    now = utc_now()
    checkpoint.set_route_state(
        "__aggregate__",
        route_fingerprint=fingerprint,
        status="in_progress",
        state={
            "candidates": [candidate.to_dict() for candidate in result.candidates],
            "duplicates": [candidate.to_dict() for candidate in result.duplicates],
            "route_counts": dict(result.route_counts),
        },
        updated_at=now,
    )
    route_fingerprint = stable_fingerprint(asdict(route))
    state = {"candidate_count": result.route_counts.get(route.origin, 0)}
    if route.origin in result.completed_routes:
        checkpoint.mark_route_completed(
            route.origin,
            route_fingerprint=route_fingerprint,
            state=state,
            updated_at=now,
        )
    else:
        matching = [error for error in result.errors if error.route == route.origin]
        checkpoint.mark_route_failed(
            route.origin,
            route_fingerprint=route_fingerprint,
            state=state,
            updated_at=now,
            error=matching[-1].message if matching else "Route did not complete.",
        )
    save_checkpoint(checkpoint, path)


def complete_checkpoint(
    checkpoint: DiscoveryCheckpoint,
    path: Path,
    fingerprint: str,
    result: DiscoveryResult,
    *,
    all_routes_completed: bool = True,
) -> None:
    checkpoint.set_route_state(
        "__aggregate__",
        route_fingerprint=fingerprint,
        status="completed" if all_routes_completed else "in_progress",
        state={
            "candidates": [candidate.to_dict() for candidate in result.candidates],
            "duplicates": [candidate.to_dict() for candidate in result.duplicates],
            "route_counts": dict(result.route_counts),
        },
        updated_at=utc_now(),
    )
    save_checkpoint(checkpoint, path)


def registry_pipeline_version(options: Any) -> str:
    return (
        f"{PIPELINE_VERSION}:comments={int(options.include_comments)}:"
        f"max_comments={options.max_comments_per_post}"
    )


def registry_scoring_version(options: Any) -> str:
    limit = options.accepted_limit if options.accepted_limit is not None else "none"
    return (
        f"{SCORING_VERSION}:threshold={options.minimum_usefulness_score}:"
        f"accepted_limit={limit}"
    )


def registry_candidates(
    registry: SourceRegistry, record_kind: str | None = None
) -> list[RedditCandidate]:
    candidates: list[RedditCandidate] = []
    for record in registry.carried_records(record_kind):
        try:
            candidates.append(RedditCandidate.from_dict(record))
        except (TypeError, ValueError):
            continue
    return candidates


def refresh_carried_metadata(
    carried: RedditCandidate, observed: RedditCandidate
) -> None:
    for field_name in (
        "fullname",
        "subreddit",
        "title",
        "selftext",
        "canonical_url",
        "permalink",
        "author_name",
        "created_utc",
        "retrieved_at",
        "score",
        "upvote_ratio",
        "num_comments",
        "link_flair_text",
        "is_self",
        "is_original_content",
        "over_18",
        "spoiler",
        "stickied",
        "distinguished",
        "locked",
        "archived",
        "removed_by_category",
        "external_url",
        "acquisition_method",
        "provenance",
        "content_hash",
    ):
        setattr(carried, field_name, getattr(observed, field_name))


def mark_not_scored(candidate: RedditCandidate, reason: str) -> None:
    candidate.hard_rejection_reason = reason if not reason.startswith("DUPLICATE_") else None
    candidate.rejection_reason = reason
    candidate.processing_status = "rejected"
    candidate.score_breakdown = {"status": "not_scored", "reason": reason}
    candidate.heuristic_score = 0
    candidate.final_usefulness_score = 0
    candidate.requires_human_review = False


def classify_retained(
    dedupe: DedupeResult,
    registry: SourceRegistry,
    processed_ids: set[str],
    threshold: int,
) -> tuple[list[RedditCandidate], list[RedditCandidate], list[RedditCandidate]]:
    accepted: list[RedditCandidate] = []
    review: list[RedditCandidate] = []
    rejected: list[RedditCandidate] = []
    buckets = {"accepted": accepted, "human_review": review, "rejected": rejected}
    for candidate in dedupe.retained:
        if candidate.reddit_post_id in processed_ids:
            decision = classify_candidate(
                candidate,
                minimum_usefulness_score=threshold,
                llm_active=False,
            )
            candidate.processing_status = decision.status
            candidate.rejection_reason = (
                None if decision.status == "accepted" else decision.reason_code
            )
            candidate.requires_human_review = decision.status == "human_review"
        else:
            entry = registry.get(candidate.reddit_post_id) or {}
            status = str(entry.get("record_kind", candidate.processing_status))
            candidate.processing_status = status if status in buckets else "rejected"
        buckets[candidate.processing_status].append(candidate)
    return rank_candidates(accepted), rank_candidates(review), rejected


def rank_candidates(candidates: Iterable[RedditCandidate]) -> list[RedditCandidate]:
    return sorted(
        candidates,
        key=lambda item: (
            -item.final_usefulness_score,
            -_score_component(item, "content_depth"),
            -_score_component(item, "actionability"),
            -_score_component(item, "engagement_signal"),
            item.title.casefold(),
            item.reddit_post_id,
        ),
    )


def apply_accepted_limit(
    candidates: list[RedditCandidate], limit: int
) -> tuple[list[RedditCandidate], list[RedditCandidate]]:
    ranked = rank_candidates(candidates)
    kept, overflow = ranked[:limit], ranked[limit:]
    for candidate in overflow:
        candidate.processing_status = "human_review"
        candidate.requires_human_review = True
        candidate.rejection_reason = "ACCEPTED_LIMIT_REACHED"
    return kept, overflow


def record_candidate(
    registry: SourceRegistry,
    candidate: RedditCandidate,
    status: str,
    *,
    incomplete_reason: str | None = None,
) -> None:
    ensure_resource_id(candidate)
    registry.record_result(
        candidate.reddit_post_id,
        processed_at=utc_now(),
        processing_status="failed" if incomplete_reason else status,
        accepted=status == "accepted",
        final_usefulness_score=candidate.final_usefulness_score,
        output_resource_id=candidate.resource_id if status == "accepted" else None,
        failure_reason=(
            incomplete_reason
            or (candidate.rejection_reason if status == "rejected" else None)
        ),
        record_data=candidate,
        record_kind=status,
    )


def unique_candidates(candidates: Iterable[RedditCandidate]) -> list[RedditCandidate]:
    values: dict[str, RedditCandidate] = {}
    for candidate in candidates:
        key = candidate.reddit_post_id or ensure_resource_id(candidate)
        values[key] = candidate
    return [values[key] for key in sorted(values)]


def _score_component(candidate: RedditCandidate, name: str) -> int:
    value = candidate.score_breakdown.get(name, 0)
    if isinstance(value, dict):
        value = value.get("score", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def exact_duplicate_clusters(
    duplicates: Iterable[RedditCandidate], retained: Iterable[RedditCandidate]
) -> list[dict[str, Any]]:
    retained_by_id = {candidate.reddit_post_id: candidate for candidate in retained}
    groups: dict[str, list[RedditCandidate]] = {}
    for candidate in duplicates:
        retained_id = (candidate.duplicate_of or "").removeprefix("reddit_")
        groups.setdefault(retained_id, []).append(candidate)
    clusters: list[dict[str, Any]] = []
    for retained_id, members in sorted(groups.items()):
        representative = retained_by_id.get(retained_id)
        retained_resource_id = f"reddit_{retained_id}"
        member_ids = [retained_resource_id, *(ensure_resource_id(item) for item in members)]
        reasons = sorted(
            {
                item.rejection_reason or "DUPLICATE_CANONICAL_URL"
                for item in members
            }
        )
        seed = "\n".join([*sorted(member_ids), *reasons])
        clusters.append(
            {
                "cluster_id": f"duplicate_{hashlib.sha256(seed.encode()).hexdigest()[:16]}",
                "retained_resource_id": retained_resource_id,
                "retained_reddit_post_id": retained_id,
                "member_resource_ids": sorted(set(member_ids)),
                "members": [
                    {
                        "candidate_resource_id": ensure_resource_id(item),
                        "reddit_post_id": item.reddit_post_id,
                        "title": item.title,
                        "canonical_url": item.canonical_url,
                        "reason_code": item.rejection_reason,
                        "duplicate_of_resource_id": retained_resource_id,
                        "similarity": (
                            1.0
                            if item.rejection_reason
                            in {"DUPLICATE_POST_ID", "DUPLICATE_CANONICAL_URL"}
                            else None
                        ),
                        "discovered_by": list(item.discovered_by),
                    }
                    for item in members
                ],
                "match_reasons": [reason.casefold() for reason in reasons],
                "retained_title": representative.title if representative else "",
            }
        )
    return clusters


def archive_existing_outputs(output_dir: Path, timestamp: str) -> None:
    existing = [path for path in output_paths(output_dir).values() if path.exists()]
    checkpoint = output_dir / ".reddit_discovery_checkpoint.json"
    if checkpoint.exists():
        existing.append(checkpoint)
    if not existing:
        return
    archive_root = output_dir / "history"
    archive_name = timestamp.replace(":", "-")
    archive = archive_root / archive_name
    suffix = 2
    while archive.exists():
        archive = archive_root / f"{archive_name}-{suffix}"
        suffix += 1
    archive.mkdir(parents=True, exist_ok=True)
    for source in existing:
        shutil.copy2(source, archive / source.name)
    _prune_output_history(archive_root, keep=2)


def _prune_output_history(archive_root: Path, *, keep: int) -> None:
    if keep < 0:
        raise ValueError("keep cannot be negative.")
    resolved_root = archive_root.resolve()
    archives = sorted(
        (
            path
            for path in archive_root.iterdir()
            if path.is_dir() and not path.is_symlink()
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    for archive in archives[keep:]:
        resolved_archive = archive.resolve()
        try:
            resolved_archive.relative_to(resolved_root)
        except ValueError:
            continue
        shutil.rmtree(resolved_archive)
