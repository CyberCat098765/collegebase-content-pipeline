from __future__ import annotations

import time
from pathlib import Path

from src import __version__
from src.config import PipelineConfig, load_config
from src.job_executor import collect_job_with_retries, error_source_type
from src.models import PipelineError, PipelineOutput, SourceItem
from src.output.json_loader import load_pipeline_parts
from src.processing.deduper import dedupe_items
from src.processing.pipeline import (
    empty_processing_stats,
    merge_processing_stats,
    process_items,
)
from src.runtime import (
    SourceJob,
    build_source_jobs,
    credential_skips,
    invalid_job_sources,
    load_jobs,
    load_source_registry,
    reset_jobs,
    save_checkpoint,
    save_jobs,
    save_source_registry,
    source_succeeded_before,
    update_source_registry,
)
from src.time_utils import utc_now

def run_pipeline(
    config_path: str | Path,
    collected_at: str | None = None,
    resume: bool = False,
    force: bool = False,
    out_path: str | Path | None = None,
) -> PipelineOutput:
    config = load_config(config_path)
    collected_at = collected_at or utc_now()
    started_monotonic = time.monotonic()
    runtime_dir = Path(config.run.runtime_dir)
    jobs = _load_or_create_jobs(config, runtime_dir, collected_at, resume, force)
    raise_for_invalid_jobs(jobs)
    source_registry = load_source_registry(runtime_dir)

    items, errors = _load_resume_output(out_path, resume=resume and not force)
    processing_stats = empty_processing_stats()
    stop_reason = "completed"
    credential_skip_messages = credential_skips(config, jobs)
    processed_this_run = 0

    save_jobs(jobs, runtime_dir)
    save_source_registry(source_registry, runtime_dir)
    for job in jobs:
        if resume and not force and job.status in {"succeeded", "skipped", "failed"}:
            continue

        stop_reason = _budget_stop_reason(
            config=config,
            jobs=jobs,
            processed_this_run=processed_this_run,
            chunk_count=sum(len(item.chunks) for item in items),
            started_monotonic=started_monotonic,
        )
        if stop_reason != "completed":
            break

        if not force and source_succeeded_before(source_registry, job):
            _mark_job(
                job,
                "skipped",
                collected_at,
                "Skipped from source registry cache; pass --force to reprocess.",
            )
            update_source_registry(source_registry, job, collected_at)
            save_source_registry(source_registry, runtime_dir)
            processed_this_run += 1
            _checkpoint_if_needed(config, jobs, runtime_dir, processed_this_run, stop_reason)
            continue

        if job.source_type in credential_skip_messages:
            _mark_job(job, "skipped", collected_at, credential_skip_messages[job.source_type])
            errors.append(PipelineError(error_source_type(job), job.url, job.error))
            update_source_registry(source_registry, job, collected_at)
            save_source_registry(source_registry, runtime_dir)
            processed_this_run += 1
            _checkpoint_if_needed(config, jobs, runtime_dir, processed_this_run, stop_reason)
            continue

        collected_items, job_errors = collect_job_with_retries(config, job, collected_at)
        processed_items, processing_errors, job_stats = process_items(
            items=dedupe_items(collected_items),
            max_chars=config.chunk_max_chars,
        )
        merge_processing_stats(processing_stats, job_stats)
        errors.extend(job_errors)
        errors.extend(processing_errors)

        if processed_items:
            items.extend(processed_items)
            _mark_job(job, "succeeded", collected_at, "")
        else:
            message = _first_error_message(job_errors, processing_errors)
            _mark_job(job, "failed", collected_at, message or "No items were collected.")
        update_source_registry(source_registry, job, collected_at)
        save_source_registry(source_registry, runtime_dir)

        processed_this_run += 1
        _checkpoint_if_needed(config, jobs, runtime_dir, processed_this_run, stop_reason)
        if config.run.request_delay_seconds > 0:
            time.sleep(config.run.request_delay_seconds)

    stop_reason = _final_stop_reason(config, jobs, stop_reason, started_monotonic, items)
    save_jobs(jobs, runtime_dir)
    save_source_registry(source_registry, runtime_dir)
    save_checkpoint(
        jobs,
        runtime_dir,
        {
            "pipeline_version": __version__,
            "started_at": collected_at,
            "updated_at": utc_now(),
            "stop_reason": stop_reason,
        },
    )

    output_items = dedupe_items(items)
    run_summary = build_run_summary(
        config=config,
        items=output_items,
        errors=errors,
        processing_stats=processing_stats,
        jobs=jobs,
        started_at=collected_at,
        finished_at=utc_now(),
        runtime_seconds=round(time.monotonic() - started_monotonic, 2),
        stop_reason=stop_reason,
        source_registry=source_registry,
    )
    return PipelineOutput(
        pipeline_version=__version__,
        collected_at=collected_at,
        run_summary=run_summary,
        items=output_items,
        errors=errors,
    )


def build_run_summary(
    config: PipelineConfig,
    items: list[SourceItem],
    errors: list[PipelineError],
    processing_stats: dict[str, int] | None = None,
    jobs: list[SourceJob] | None = None,
    started_at: str = "",
    finished_at: str = "",
    runtime_seconds: float = 0.0,
    stop_reason: str = "completed",
    source_registry: dict[str, object] | None = None,
) -> dict[str, object]:
    attempted = attempt_counts(config)
    succeeded: dict[str, int] = {}
    failed: dict[str, int] = {}
    processing_stats = processing_stats or {}
    jobs = jobs or []
    job_status_counts = _job_status_counts(jobs)
    sources_succeeded = (
        job_status_counts.get("succeeded", 0) if jobs else len(items)
    )
    sources_failed = job_status_counts.get("failed", 0) if jobs else len(errors)
    sources_skipped = job_status_counts.get("skipped", 0) if jobs else 0
    sources_skipped_from_cache = _count_cached_skips(jobs)
    sources_skipped_due_to_credentials = _count_credential_skips(jobs)
    registry_sources = 0
    if isinstance(source_registry, dict) and isinstance(source_registry.get("sources"), dict):
        registry_sources = len(source_registry["sources"])

    for item in items:
        succeeded[item.source_type] = succeeded.get(item.source_type, 0) + 1
    for error in errors:
        failed[error.source_type] = failed.get(error.source_type, 0) + 1

    youtube_manual_count = sum(
        1
        for item in items
        if item.source_type == "youtube"
        and item.metadata.get("transcript_source") == "manual_file"
    )
    youtube_live_count = sum(
        1
        for item in items
        if item.source_type == "youtube"
        and item.metadata.get("transcript_source") != "manual_file"
    )

    return {
        "started_at": started_at,
        "finished_at": finished_at,
        "runtime_seconds": runtime_seconds,
        "sources_attempted": len(jobs) if jobs else sum(attempted.values()),
        "sources_succeeded": sources_succeeded,
        "sources_failed": sources_failed,
        "sources_skipped": sources_skipped,
        "sources_skipped_from_cache": sources_skipped_from_cache,
        "sources_skipped_due_to_credentials": sources_skipped_due_to_credentials,
        "chunks_generated": sum(len(item.chunks) for item in items),
        "chunks_kept": sum(len(item.chunks) for item in items),
        "chunks_dropped": processing_stats.get("chunks_dropped_by_admissions_filter", 0),
        "admissions_relevant_chunks": sum(len(item.chunks) for item in items),
        "chunks_before_admissions_filter": processing_stats.get(
            "chunks_before_admissions_filter", 0
        ),
        "chunks_dropped_by_admissions_filter": processing_stats.get(
            "chunks_dropped_by_admissions_filter", 0
        ),
        "sources_dropped_by_admissions_filter": processing_stats.get(
            "sources_dropped_by_admissions_filter", 0
        ),
        "collectors": _collector_summary(attempted, succeeded, failed, youtube_live_count),
        "collectors_proven_in_this_run": [
            source_type
            for source_type, count in succeeded.items()
            if count > 0 and (source_type != "youtube" or youtube_live_count > 0)
        ],
        "manual_transcript_fallback_proven": youtube_manual_count > 0,
        "youtube_live_transcript_items": youtube_live_count,
        "youtube_manual_transcript_items": youtube_manual_count,
        "collectors_skipped_due_to_missing_credentials": [
            error.source_type
            for error in errors
            if "credentials" in error.message.lower()
        ],
        "failure_reasons": [error.to_dict() for error in errors],
        "errors": [error.to_dict() for error in errors],
        "stop_reason": stop_reason,
        "source_jobs": [job.to_dict() for job in jobs],
        "source_registry": {
            "path": str(Path(config.run.runtime_dir) / "source_registry.json"),
            "registered_sources": registry_sources,
        },
    }


def attempt_counts(config: PipelineConfig) -> dict[str, int]:
    return {
        "blog": len(config.articles.urls),
        "youtube": len(config.youtube.videos) + len(config.youtube.transcript_files),
        "reddit": len(config.reddit.urls)
        + len(config.reddit.subreddits) * len(config.reddit.keywords),
        "official": len(config.official.college_scorecard.schools)
        if config.official.college_scorecard.enabled
        else 0,
    }


def raise_for_invalid_jobs(jobs: list[SourceJob]) -> None:
    invalid_sources = invalid_job_sources(jobs)
    if invalid_sources:
        raise SystemExit("Invalid source entries: " + "; ".join(invalid_sources))


def _load_or_create_jobs(
    config: PipelineConfig,
    runtime_dir: Path,
    collected_at: str,
    resume: bool,
    force: bool,
) -> list[SourceJob]:
    if resume:
        jobs = load_jobs(runtime_dir)
        if jobs:
            return reset_jobs(jobs, collected_at) if force else jobs
    return build_source_jobs(config, collected_at)


def _load_resume_output(
    out_path: str | Path | None,
    resume: bool,
) -> tuple[list[SourceItem], list[PipelineError]]:
    if not resume or not out_path:
        return [], []
    return load_pipeline_parts(out_path)


def _collector_summary(
    attempted: dict[str, int],
    succeeded: dict[str, int],
    failed: dict[str, int],
    youtube_live_count: int,
) -> dict[str, dict[str, object]]:
    collectors: dict[str, dict[str, object]] = {}
    for source_type in ["blog", "youtube", "reddit", "official"]:
        attempted_count = attempted.get(source_type, 0)
        succeeded_count = succeeded.get(source_type, 0)
        failed_count = failed.get(source_type, 0)
        if source_type == "youtube" and succeeded_count and not youtube_live_count:
            status = "manual_transcript_fallback_proven"
        elif succeeded_count:
            status = "proven_in_this_run"
        elif attempted_count and failed_count:
            status = "failed_or_skipped"
        elif attempted_count:
            status = "attempted_no_items"
        else:
            status = "not_configured"
        collectors[source_type] = {
            "attempted": attempted_count,
            "succeeded": succeeded_count,
            "failed": failed_count,
            "status": status,
        }
    return collectors


def _budget_stop_reason(
    config: PipelineConfig,
    jobs: list[SourceJob],
    processed_this_run: int,
    chunk_count: int,
    started_monotonic: float,
) -> str:
    if time.monotonic() - started_monotonic >= config.run.max_runtime_minutes * 60:
        return "max_runtime_reached"
    if processed_this_run >= config.run.max_sources_total:
        return "max_sources_reached"
    if chunk_count >= config.run.max_chunks_total:
        return "max_chunks_reached"
    if sum(1 for job in jobs if job.status == "failed") >= config.run.max_failures_total:
        return "max_failures_reached"
    return "completed"


def _final_stop_reason(
    config: PipelineConfig,
    jobs: list[SourceJob],
    current_reason: str,
    started_monotonic: float,
    items: list[SourceItem],
) -> str:
    if current_reason != "completed":
        return current_reason
    return _budget_stop_reason(
        config=config,
        jobs=jobs,
        processed_this_run=sum(1 for job in jobs if job.status in {"succeeded", "failed", "skipped"}),
        chunk_count=sum(len(item.chunks) for item in items),
        started_monotonic=started_monotonic,
    )


def _checkpoint_if_needed(
    config: PipelineConfig,
    jobs: list[SourceJob],
    runtime_dir: Path,
    processed_this_run: int,
    stop_reason: str,
) -> None:
    if processed_this_run % config.run.checkpoint_every_n_sources != 0:
        return
    save_jobs(jobs, runtime_dir)
    save_checkpoint(
        jobs,
        runtime_dir,
        {"pipeline_version": __version__, "updated_at": utc_now(), "stop_reason": stop_reason},
    )


def _mark_job(job: SourceJob, status: str, updated_at: str, error: str) -> None:
    job.status = status
    job.updated_at = updated_at
    job.error = error


def _first_error_message(
    collector_errors: list[PipelineError],
    processing_errors: list[PipelineError],
) -> str:
    for error in [*collector_errors, *processing_errors]:
        if error.message:
            return error.message
    return ""


def _job_status_counts(jobs: list[SourceJob]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for job in jobs:
        counts[job.status] = counts.get(job.status, 0) + 1
    return counts


def _count_cached_skips(jobs: list[SourceJob]) -> int:
    return sum(
        1
        for job in jobs
        if job.status == "skipped" and "source registry cache" in job.error.lower()
    )


def _count_credential_skips(jobs: list[SourceJob]) -> int:
    return sum(
        1
        for job in jobs
        if job.status == "skipped" and "credentials" in job.error.lower()
    )
