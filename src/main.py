from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from src.config import PipelineConfig, load_config
from src.output.content_briefs import build_content_briefs, write_content_briefs
from src.output.json_writer import write_pipeline_output
from src.runner import attempt_counts, raise_for_invalid_jobs, run_pipeline
from src.runtime import (
    SourceJob,
    build_source_jobs,
    credential_skips,
    invalid_job_sources,
    job_counts,
)
from src.time_utils import utc_now


def main() -> None:
    args = _parse_args()
    _configure_logging(verbose=args.verbose)

    if args.validate_only:
        config = load_config(args.config)
        jobs = build_source_jobs(config, utc_now())
        _print_validation_summary(config, jobs)
        raise_for_invalid_jobs(jobs)
        return

    if args.dry_run:
        config = load_config(args.config)
        jobs = build_source_jobs(config, utc_now())
        _print_dry_run_summary(config, jobs)
        raise_for_invalid_jobs(jobs)
        return

    if not args.out:
        raise SystemExit("--out is required unless --validate-only or --dry-run is used.")

    collected_at = utc_now()
    output = run_pipeline(
        config_path=args.config,
        collected_at=collected_at,
        resume=args.resume,
        force=args.force,
        out_path=args.out,
    )
    write_pipeline_output(output, args.out)
    _write_run_summary(output.run_summary, args.out)
    if args.briefs:
        briefs = build_content_briefs(output, generated_at=collected_at)
        write_content_briefs(briefs, args.briefs)
    _print_run_summary(output.run_summary, args.out, args.briefs)


def _print_run_summary(
    summary: dict[str, object],
    out_path: str,
    briefs_path: str | None,
) -> None:
    print("Run summary")
    print(f"- Output: {out_path}")
    if briefs_path:
        print(f"- Content briefs: {briefs_path}")
    print(f"- Sources attempted: {summary.get('sources_attempted', 0)}")
    print(f"- Sources succeeded: {summary.get('sources_succeeded', 0)}")
    print(f"- Sources failed: {summary.get('sources_failed', 0)}")
    print(f"- Sources skipped: {summary.get('sources_skipped', 0)}")
    print(f"- Sources skipped from cache: {summary.get('sources_skipped_from_cache', 0)}")
    print(
        "- Sources skipped due to credentials/access: "
        f"{summary.get('sources_skipped_due_to_credentials', 0)}"
    )
    print(f"- Admissions-relevant chunks: {summary.get('admissions_relevant_chunks', 0)}")
    print(
        "- Chunks dropped by admissions filter: "
        f"{summary.get('chunks_dropped_by_admissions_filter', 0)}"
    )
    print(f"- Stop reason: {summary.get('stop_reason', 'completed')}")
    print(
        "- Collectors proven: "
        + ", ".join(summary.get("collectors_proven_in_this_run", []) or ["none"])
    )
    print(f"- Manual transcript fallback proven: {summary.get('manual_transcript_fallback_proven', False)}")
    print(f"- Live YouTube transcript items: {summary.get('youtube_live_transcript_items', 0)}")


def _write_run_summary(summary: dict[str, object], out_path: str | Path) -> None:
    path = Path(out_path).parent / "run_summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _print_validation_summary(config: PipelineConfig, jobs: list[SourceJob]) -> None:
    attempted = attempt_counts(config)
    missing_credentials = credential_skips(config, jobs)
    invalid_sources = invalid_job_sources(jobs)
    print("Config validation passed")
    print(f"- Article URLs: {attempted['blog']}")
    print(f"- YouTube URLs: {len(config.youtube.videos)}")
    print(f"- Manual transcript files: {len(config.youtube.transcript_files)}")
    print(f"- Reddit attempts: {attempted['reddit']}")
    print(f"- Official data attempts: {attempted['official']}")
    print(f"- Source jobs: {len(jobs)}")
    print(f"- Run max sources: {config.run.max_sources_total}")
    print(f"- Run max chunks: {config.run.max_chunks_total}")
    print(f"- Retries per source: {config.run.retries_per_source}")
    print(f"- Runtime dir: {config.run.runtime_dir}")
    print(
        "- Credential-gated skips: "
        + ", ".join(missing_credentials.keys() or ["none"])
    )
    print(f"- Invalid source entries: {len(invalid_sources)}")


def _print_dry_run_summary(config: PipelineConfig, jobs: list[SourceJob]) -> None:
    counts = job_counts(jobs)
    missing_credentials = credential_skips(config, jobs)
    print("Dry run")
    print(f"- Jobs that would be created: {len(jobs)}")
    print(
        "- Collectors that would run: "
        + ", ".join(sorted(counts.keys()) or ["none"])
    )
    for source_type in sorted(counts):
        print(f"- {source_type} jobs: {counts[source_type]}")
    print(
        "- Sources skipped due to missing credentials: "
        + ", ".join(missing_credentials.keys() or ["none"])
    )
    print(f"- Run max sources: {config.run.max_sources_total}")
    print(f"- Run max chunks: {config.run.max_chunks_total}")
    print("- No scraping performed")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect and process admissions content into citation-ready JSON."
    )
    parser.add_argument("--config", required=True, help="Path to a YAML or JSON source config.")
    parser.add_argument("--out", help="Path to write processed JSON.")
    parser.add_argument("--briefs", help="Optional path to write content briefs JSON.")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate config shape and print source counts without collecting.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show source jobs and credential-gated skips without collecting.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from data/runtime/jobs.json without reprocessing successful jobs.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="With --resume, reprocess all jobs instead of preserving successful jobs.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


if __name__ == "__main__":
    main()
