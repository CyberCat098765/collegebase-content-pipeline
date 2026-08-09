from __future__ import annotations

import copy
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.reddit_discovery.bundle_storage import write_bundle_files
from src.reddit_discovery.review import build_review_report


OUTPUT_FILENAMES = {
    "raw_candidates": "raw_candidates.jsonl",
    "accepted_resources": "accepted_resources.json",
    "human_review": "human_review.json",
    "rejected_candidates": "rejected_candidates.jsonl",
    "duplicate_clusters": "duplicate_clusters.json",
    "source_registry": "source_registry.json",
    "run_summary": "run_summary.json",
    "review_report": "review_report.md",
}

ACCEPTED_RESOURCE_FIELDS = (
    "resource_id",
    "source_platform",
    "subreddit",
    "reddit_post_id",
    "canonical_url",
    "permalink",
    "external_url",
    "title",
    "cleaned_text",
    "author",
    "created_at",
    "retrieved_at",
    "score",
    "upvote_ratio",
    "comment_count",
    "flair",
    "primary_topic",
    "secondary_topics",
    "audience",
    "summary",
    "key_takeaways",
    "why_useful",
    "limitations_or_cautions",
    "freshness_status",
    "heuristic_score",
    "llm_adjustment",
    "final_usefulness_score",
    "score_breakdown",
    "confidence",
    "selection_reasons",
    "discovered_by",
    "acquisition_method",
    "provenance",
    "content_hash",
    "pipeline_version",
    "comments",
)

TOPICS = frozenset(
    {
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
    }
)


class OutputValidationError(ValueError):
    pass


@dataclass(slots=True)
class OutputBundle:
    raw_candidates: list[dict[str, Any]]
    accepted_resources: list[dict[str, Any]]
    human_review: list[dict[str, Any]]
    rejected_candidates: list[dict[str, Any]]
    duplicate_clusters: list[dict[str, Any]]
    source_registry: dict[str, Any]
    run_summary: dict[str, Any]
    review_report: str


def accepted_resource_from_candidate(
    candidate: Mapping[str, Any] | object,
    *,
    pipeline_version: str = "",
) -> dict[str, Any]:
    """Project an evaluated candidate onto the required resource schema."""

    resource_serializer = getattr(candidate, "to_resource_dict", None)
    if callable(resource_serializer):
        serialized = (
            resource_serializer(pipeline_version)
            if pipeline_version
            else resource_serializer()
        )
        if isinstance(serialized, Mapping):
            value = dict(serialized)
            if all(field in value for field in ACCEPTED_RESOURCE_FIELDS):
                return {
                    field: copy.deepcopy(value[field])
                    for field in ACCEPTED_RESOURCE_FIELDS
                }

    value = _as_mapping(candidate)
    if all(field in value for field in ACCEPTED_RESOURCE_FIELDS):
        return {field: copy.deepcopy(value[field]) for field in ACCEPTED_RESOURCE_FIELDS}

    post_id = str(value.get("reddit_post_id", ""))
    return {
        "resource_id": value.get("resource_id") or (f"reddit_{post_id}" if post_id else ""),
        "source_platform": "reddit",
        "subreddit": value.get("subreddit", "ApplyingToCollege"),
        "reddit_post_id": post_id,
        "canonical_url": value.get("canonical_url", ""),
        "permalink": value.get("permalink", value.get("canonical_url", "")),
        "external_url": value.get("external_url"),
        "title": value.get("title", ""),
        "cleaned_text": value.get("cleaned_text", value.get("selftext", "")),
        "author": value.get("author", value.get("author_name")),
        "created_at": _created_at(value.get("created_at", value.get("created_utc"))),
        "retrieved_at": value.get("retrieved_at", ""),
        "score": value.get("score", 0),
        "upvote_ratio": value.get("upvote_ratio"),
        "comment_count": value.get("comment_count", value.get("num_comments", 0)),
        "flair": value.get("flair", value.get("link_flair_text")),
        "primary_topic": value.get("primary_topic", "other"),
        "secondary_topics": copy.deepcopy(value.get("secondary_topics", [])),
        "audience": copy.deepcopy(value.get("audience", [])),
        "summary": value.get("summary", ""),
        "key_takeaways": copy.deepcopy(value.get("key_takeaways", [])),
        "why_useful": value.get("why_useful", ""),
        "limitations_or_cautions": copy.deepcopy(
            value.get("limitations_or_cautions", [])
        ),
        "freshness_status": value.get("freshness_status", "durable"),
        "heuristic_score": value.get("heuristic_score", 0),
        "llm_adjustment": value.get("llm_adjustment", 0),
        "final_usefulness_score": value.get("final_usefulness_score", 0),
        "score_breakdown": copy.deepcopy(value.get("score_breakdown", {})),
        "confidence": value.get("confidence", 0.0),
        "selection_reasons": copy.deepcopy(value.get("selection_reasons", [])),
        "discovered_by": copy.deepcopy(value.get("discovered_by", [])),
        "acquisition_method": value.get("acquisition_method", ""),
        "provenance": copy.deepcopy(value.get("provenance", {})),
        "content_hash": value.get("content_hash", ""),
        "pipeline_version": value.get("pipeline_version", pipeline_version),
        "comments": copy.deepcopy(value.get("comments", [])),
    }


def build_output_bundle(
    *,
    raw_candidates: Iterable[Mapping[str, Any] | object] = (),
    accepted_resources: Iterable[Mapping[str, Any] | object] = (),
    human_review: Iterable[Mapping[str, Any] | object] = (),
    rejected_candidates: Iterable[Mapping[str, Any] | object] = (),
    duplicate_clusters: Iterable[Mapping[str, Any] | object] | object = (),
    source_registry: Mapping[str, Any] | object | None = None,
    run_summary: Mapping[str, Any] | None = None,
    review_report: str | None = None,
    pipeline_version: str = "",
) -> OutputBundle:
    raw = [_as_mapping(item) for item in raw_candidates]
    accepted = [
        accepted_resource_from_candidate(item, pipeline_version=pipeline_version)
        for item in accepted_resources
    ]
    review = [_as_mapping(item) for item in human_review]
    rejected = [_as_mapping(item) for item in rejected_candidates]
    clusters = [_as_mapping(item) for item in _cluster_records(duplicate_clusters)]
    registry = _as_mapping(source_registry) if source_registry is not None else {"posts": {}}
    summary = (
        copy.deepcopy(dict(run_summary))
        if run_summary is not None
        else build_run_summary(
            raw_candidates=raw,
            accepted_resources=accepted,
            human_review=review,
            rejected_candidates=rejected,
            duplicate_clusters=clusters,
        )
    )
    report = review_report
    if report is None:
        report = build_review_report(
            summary,
            accepted_resources=accepted,
            human_review=review,
            rejected_candidates=rejected,
            duplicate_clusters=clusters,
        )
    return OutputBundle(
        raw_candidates=raw,
        accepted_resources=accepted,
        human_review=review,
        rejected_candidates=rejected,
        duplicate_clusters=clusters,
        source_registry=registry,
        run_summary=summary,
        review_report=report,
    )


def write_output_bundle(
    bundle: OutputBundle,
    output_dir: str | Path,
    *,
    dry_run: bool = False,
    validate_only: bool = False,
    validate: bool = True,
) -> tuple[Path, ...]:
    """Explicit persistence boundary; dry/validation runs create nothing."""

    if validate:
        errors = validate_output_bundle(bundle)
        if errors:
            raise OutputValidationError("; ".join(errors))
    if dry_run or validate_only:
        return ()

    return write_bundle_files(bundle, output_paths(output_dir))


def output_paths(output_dir: str | Path) -> dict[str, Path]:
    root = Path(output_dir)
    return {key: root / filename for key, filename in OUTPUT_FILENAMES.items()}


def validate_output_bundle(bundle: OutputBundle) -> list[str]:
    errors: list[str] = []
    for index, resource in enumerate(bundle.accepted_resources):
        prefix = f"accepted_resources[{index}]"
        missing = [field for field in ACCEPTED_RESOURCE_FIELDS if field not in resource]
        if missing:
            errors.append(f"{prefix} missing fields: {', '.join(missing)}")
            continue
        if resource["source_platform"] != "reddit":
            errors.append(f"{prefix}.source_platform must be reddit")
        if resource["subreddit"].lower() != "applyingtocollege":
            errors.append(f"{prefix}.subreddit must be ApplyingToCollege")
        primary_topic = resource["primary_topic"]
        if primary_topic not in TOPICS:
            errors.append(f"{prefix}.primary_topic is not in the controlled taxonomy")
        secondary = resource["secondary_topics"]
        if not isinstance(secondary, list) or any(topic not in TOPICS for topic in secondary):
            errors.append(f"{prefix}.secondary_topics contains an invalid topic")
        score = resource["final_usefulness_score"]
        if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 100:
            errors.append(f"{prefix}.final_usefulness_score must be between 0 and 100")
        for field in (
            "audience",
            "key_takeaways",
            "limitations_or_cautions",
            "selection_reasons",
            "discovered_by",
            "comments",
        ):
            if not isinstance(resource[field], list):
                errors.append(f"{prefix}.{field} must be a list")
        for field in ("permalink", "cleaned_text", "acquisition_method"):
            if not isinstance(resource[field], str):
                errors.append(f"{prefix}.{field} must be text")
        if not isinstance(resource["score_breakdown"], Mapping):
            errors.append(f"{prefix}.score_breakdown must be an object")
        if not isinstance(resource["provenance"], Mapping):
            errors.append(f"{prefix}.provenance must be an object")
    if not isinstance(bundle.source_registry, Mapping):
        errors.append("source_registry must be an object")
    if not isinstance(bundle.run_summary, Mapping):
        errors.append("run_summary must be an object")
    if not isinstance(bundle.review_report, str):
        errors.append("review_report must be text")
    return errors


def build_run_summary(
    *,
    raw_candidates: Sequence[Mapping[str, Any]] = (),
    accepted_resources: Sequence[Mapping[str, Any]] = (),
    human_review: Sequence[Mapping[str, Any]] = (),
    rejected_candidates: Sequence[Mapping[str, Any]] = (),
    duplicate_clusters: Sequence[Mapping[str, Any]] = (),
    errors: Iterable[Mapping[str, Any] | object | str] = (),
    route_counts: Mapping[str, int] | None = None,
    started_at: str = "",
    completed_at: str = "",
    duration_seconds: float | None = None,
    subreddit: str = "ApplyingToCollege",
    mode: str = "max-range",
    skipped_unchanged_count: int = 0,
    reprocessed_changed_count: int = 0,
    dry_run: bool = False,
    validate_only: bool = False,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_errors = [_error_record(error) for error in errors]
    reason_counts = Counter(
        str(item.get("rejection_reason") or item.get("hard_rejection_reason") or "UNSPECIFIED")
        for item in rejected_candidates
    )
    all_scored = [*accepted_resources, *human_review, *rejected_candidates]
    processing_error_count = sum(
        str(error.get("route", "")).startswith(("processing:", "deduplication"))
        for error in normalized_errors
    )
    summary: dict[str, Any] = {
        "status": "completed_with_errors" if normalized_errors else "completed",
        "subreddit": subreddit,
        "mode": mode,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_seconds": (
            duration_seconds
            if duration_seconds is not None
            else _elapsed_seconds(started_at, completed_at)
        ),
        "candidate_count": len(raw_candidates),
        "accepted_count": len(accepted_resources),
        "human_review_count": len(human_review),
        "rejected_count": len(rejected_candidates),
        "duplicate_cluster_count": len(duplicate_clusters),
        "duplicate_candidate_count": sum(_duplicate_member_count(item) for item in duplicate_clusters),
        "structurally_invalid_count": sum(
            reason_counts[reason]
            for reason in ("EMPTY_TITLE", "MISSING_CONTENT", "REMOVED_CONTENT", "DELETED_CONTENT")
        ),
        "obvious_junk_rejected_count": sum(
            count
            for reason, count in reason_counts.items()
            if reason.startswith("BLOCKED_CATEGORY_")
            or reason in {"LOW_INFORMATION", "NARROW_PERSONAL_QUESTION", "TRIVIAL_TEST_SCORE_QUESTION", "SELF_PROMOTION"}
        ),
        "exact_duplicate_count": reason_counts["DUPLICATE_POST_ID"] + reason_counts["DUPLICATE_CANONICAL_URL"],
        "near_duplicate_count": reason_counts["NEAR_DUPLICATE_LOWER_QUALITY"],
        "candidates_scored_count": sum(
            isinstance(item.get("heuristic_score"), (int, float))
            and not isinstance(item.get("heuristic_score"), bool)
            and item.get("heuristic_score", 0) > 0
            for item in all_scored
        ),
        "final_selected_count": len(accepted_resources),
        "rejection_reason_counts": dict(sorted(reason_counts.items())),
        "acquisition_error_count": len(normalized_errors) - processing_error_count,
        "processing_error_count": processing_error_count,
        "cache_hit_count": skipped_unchanged_count,
        "skipped_unchanged_count": skipped_unchanged_count,
        "reprocessed_changed_count": reprocessed_changed_count,
        "route_counts": dict(route_counts or {}),
        "error_count": len(normalized_errors),
        "errors": normalized_errors,
        "dry_run": dry_run,
        "validate_only": validate_only,
    }
    if extra:
        summary.update(copy.deepcopy(dict(extra)))
    return summary


def _cluster_records(value: Iterable[Mapping[str, Any] | object] | object) -> Iterable[Any]:
    clusters = getattr(value, "clusters", None)
    return clusters if clusters is not None else value  # type: ignore[return-value]


def _as_mapping(value: Mapping[str, Any] | object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return copy.deepcopy(dict(value))
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        if isinstance(result, Mapping):
            return copy.deepcopy(dict(result))
    if is_dataclass(value):
        result = asdict(value)
        if isinstance(result, dict):
            return result
    raise TypeError(f"Expected a mapping or serializable record, got {type(value).__name__}")


def _error_record(value: Mapping[str, Any] | object | str) -> dict[str, Any]:
    if isinstance(value, str):
        return {"message": value}
    try:
        return _as_mapping(value)
    except TypeError:
        return {"message": str(value)}


def _created_at(value: Any) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return (
                datetime.fromtimestamp(value, tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
        except (OverflowError, OSError, ValueError):
            return value
    return value


def _elapsed_seconds(started_at: str, completed_at: str) -> float | None:
    if not started_at or not completed_at:
        return None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, round((end - start).total_seconds(), 3))


def _duplicate_member_count(cluster: Mapping[str, Any]) -> int:
    members = cluster.get("members", cluster.get("member_resource_ids", []))
    return len(members) if isinstance(members, list) else 0
