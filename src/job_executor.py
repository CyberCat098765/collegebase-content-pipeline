from __future__ import annotations

import logging
import time
from urllib.parse import parse_qs, urlparse

from src.config import (
    ArticleConfig,
    CollegeScorecardConfig,
    PipelineConfig,
    RedditConfig,
    YouTubeConfig,
)
from src.models import PipelineError, SourceItem
from src.runtime import SourceJob
from src.time_utils import utc_now

LOGGER = logging.getLogger(__name__)


def collect_job_with_retries(
    config: PipelineConfig,
    job: SourceJob,
    collected_at: str,
) -> tuple[list[SourceItem], list[PipelineError]]:
    max_attempts = max(1, config.run.retries_per_source + 1)
    last_errors: list[PipelineError] = []

    for attempt in range(1, max_attempts + 1):
        job.attempts = attempt
        job.status = "running"
        job.updated_at = utc_now()
        job.error = ""
        try:
            items, errors = collect_job(config, job, collected_at)
        except Exception as exc:
            LOGGER.exception("%s job failed", job.source_type)
            items = []
            errors = [PipelineError(error_source_type(job), job.url, str(exc))]

        if items or not errors:
            return items, errors

        last_errors = errors
        if attempt < max_attempts and config.run.request_delay_seconds > 0:
            time.sleep(min(config.run.request_delay_seconds * attempt, 5.0))

    return [], last_errors


def collect_job(
    config: PipelineConfig,
    job: SourceJob,
    collected_at: str,
) -> tuple[list[SourceItem], list[PipelineError]]:
    if job.source_type == "article":
        from src.collectors.article_collector import ArticleCollector

        return ArticleCollector(
            ArticleConfig(urls=[job.url], request_timeout_seconds=config.run.timeout_seconds)
        ).collect(collected_at)

    if job.source_type == "youtube":
        from src.collectors.youtube_collector import YouTubeCollector

        return YouTubeCollector(
            YouTubeConfig(
                videos=[job.url],
                transcript_files=[],
                transcript_languages=config.youtube.transcript_languages,
                request_timeout_seconds=config.run.timeout_seconds,
            )
        ).collect(collected_at)

    if job.source_type == "transcript_file":
        from src.collectors.youtube_collector import YouTubeCollector

        transcript_files = [
            transcript
            for transcript in config.youtube.transcript_files
            if transcript.source_url == job.url
        ]
        if not transcript_files:
            return [], [PipelineError("youtube", job.url, "Transcript file config not found.")]
        return YouTubeCollector(
            YouTubeConfig(
                videos=[],
                transcript_files=transcript_files,
                transcript_languages=config.youtube.transcript_languages,
                request_timeout_seconds=config.run.timeout_seconds,
            )
        ).collect(collected_at)

    if job.source_type == "reddit":
        from src.collectors.reddit_collector import RedditCollector

        return RedditCollector(_reddit_config_for_job(config, job)).collect(collected_at)

    if job.source_type == "official":
        from src.collectors.scorecard_collector import CollegeScorecardCollector

        return CollegeScorecardCollector(
            CollegeScorecardConfig(
                enabled=True,
                schools=[job.url],
                api_key_env=config.official.college_scorecard.api_key_env,
                request_timeout_seconds=config.run.timeout_seconds,
            )
        ).collect(collected_at)

    return [], [PipelineError(job.source_type, job.url, "Unsupported source job type.")]


def error_source_type(job: SourceJob) -> str:
    return "youtube" if job.source_type == "transcript_file" else job.source_type


def _reddit_config_for_job(config: PipelineConfig, job: SourceJob) -> RedditConfig:
    if job.url.startswith("reddit://search/"):
        parsed = urlparse(job.url)
        subreddit = parsed.path.strip("/")
        keyword = parse_qs(parsed.query).get("q", [""])[0]
        return RedditConfig(
            subreddits=[subreddit] if subreddit else [],
            keywords=[keyword] if keyword else [],
            urls=[],
            limit_per_query=config.reddit.limit_per_query,
            max_comments=config.reddit.max_comments,
            request_timeout_seconds=config.run.timeout_seconds,
            max_rate_limit_sleep_seconds=config.reddit.max_rate_limit_sleep_seconds,
        )

    return RedditConfig(
        subreddits=[],
        keywords=[],
        urls=[job.url],
        limit_per_query=config.reddit.limit_per_query,
        max_comments=config.reddit.max_comments,
        request_timeout_seconds=config.run.timeout_seconds,
        max_rate_limit_sleep_seconds=config.reddit.max_rate_limit_sleep_seconds,
    )
