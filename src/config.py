from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class RedditConfig:
    subreddits: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    limit_per_query: int = 10
    max_comments: int = 20
    request_timeout_seconds: int = 15
    max_rate_limit_sleep_seconds: int = 10


@dataclass(slots=True)
class YouTubeConfig:
    videos: list[str] = field(default_factory=list)
    transcript_files: list[TranscriptFileConfig] = field(default_factory=list)
    transcript_languages: list[str] = field(default_factory=lambda: ["en"])
    request_timeout_seconds: int = 15


@dataclass(slots=True)
class TranscriptFileConfig:
    path: str
    source_url: str
    title: str
    channel: str = ""
    published_date: str = ""


@dataclass(slots=True)
class ArticleConfig:
    urls: list[str] = field(default_factory=list)
    request_timeout_seconds: int = 20


@dataclass(slots=True)
class CollegeScorecardConfig:
    enabled: bool = False
    schools: list[str] = field(default_factory=list)
    api_key_env: str = "COLLEGE_SCORECARD_API_KEY"
    request_timeout_seconds: int = 20


@dataclass(slots=True)
class OfficialConfig:
    college_scorecard: CollegeScorecardConfig = field(default_factory=CollegeScorecardConfig)


@dataclass(slots=True)
class RunConfig:
    max_runtime_minutes: int = 30
    max_sources_total: int = 25
    max_chunks_total: int = 300
    max_failures_total: int = 20
    request_delay_seconds: float = 0.0
    timeout_seconds: int = 20
    retries_per_source: int = 1
    checkpoint_every_n_sources: int = 5
    runtime_dir: str = "data/runtime"


@dataclass(slots=True)
class PipelineConfig:
    reddit: RedditConfig = field(default_factory=RedditConfig)
    youtube: YouTubeConfig = field(default_factory=YouTubeConfig)
    articles: ArticleConfig = field(default_factory=ArticleConfig)
    official: OfficialConfig = field(default_factory=OfficialConfig)
    run: RunConfig = field(default_factory=RunConfig)
    chunk_max_chars: int = 1200


def load_config(path: str | Path) -> PipelineConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    data = _read_config_file(config_path)
    if not isinstance(data, dict):
        raise ValueError("Config file must contain a JSON/YAML object.")

    sources = data.get("sources", {})
    if not isinstance(sources, dict):
        raise ValueError("Config field 'sources' must be an object.")

    processing = data.get("processing", {})
    if processing is None:
        processing = {}
    if not isinstance(processing, dict):
        raise ValueError("Config field 'processing' must be an object when provided.")

    run_data = data.get("run", {})
    if run_data is None:
        run_data = {}
    if not isinstance(run_data, dict):
        raise ValueError("Config field 'run' must be an object when provided.")

    reddit_data = _section(sources, "reddit")
    youtube_data = _section(sources, "youtube")
    articles_data = _section(sources, "articles")
    official_data = _section(sources, "official")
    scorecard_data = _section(official_data, "college_scorecard")

    return PipelineConfig(
        reddit=RedditConfig(
            subreddits=_string_list(reddit_data.get("subreddits")),
            keywords=_string_list(reddit_data.get("keywords")),
            urls=_string_list(reddit_data.get("urls")),
            limit_per_query=_positive_int(reddit_data.get("limit_per_query"), 10),
            max_comments=_positive_int(reddit_data.get("max_comments"), 20),
            request_timeout_seconds=_positive_int(
                reddit_data.get("request_timeout_seconds"), 15
            ),
            max_rate_limit_sleep_seconds=_positive_int(
                reddit_data.get("max_rate_limit_sleep_seconds"), 10
            ),
        ),
        youtube=YouTubeConfig(
            videos=_youtube_videos(youtube_data.get("videos")),
            transcript_files=_transcript_files(
                youtube_data.get("transcript_files"),
                base_dir=config_path.parent,
            ),
            transcript_languages=_string_list(
                youtube_data.get("transcript_languages"), default=["en"]
            ),
            request_timeout_seconds=_positive_int(
                youtube_data.get("request_timeout_seconds"), 15
            ),
        ),
        articles=ArticleConfig(
            urls=_string_list(articles_data.get("urls")),
            request_timeout_seconds=_positive_int(
                articles_data.get("request_timeout_seconds"), 20
            ),
        ),
        official=OfficialConfig(
            college_scorecard=CollegeScorecardConfig(
                enabled=bool(scorecard_data.get("enabled", False)),
                schools=_string_list(scorecard_data.get("schools")),
                api_key_env=str(
                    scorecard_data.get("api_key_env", "COLLEGE_SCORECARD_API_KEY")
                ),
                request_timeout_seconds=_positive_int(
                    scorecard_data.get("request_timeout_seconds"), 20
                ),
            )
        ),
        run=RunConfig(
            max_runtime_minutes=_positive_int(run_data.get("max_runtime_minutes"), 30),
            max_sources_total=_positive_int(run_data.get("max_sources_total"), 25),
            max_chunks_total=_positive_int(run_data.get("max_chunks_total"), 300),
            max_failures_total=_positive_int(run_data.get("max_failures_total"), 20),
            request_delay_seconds=_non_negative_float(
                run_data.get("request_delay_seconds"), 0.0
            ),
            timeout_seconds=_positive_int(run_data.get("timeout_seconds"), 20),
            retries_per_source=_non_negative_int(run_data.get("retries_per_source"), 1),
            checkpoint_every_n_sources=_positive_int(
                run_data.get("checkpoint_every_n_sources"), 5
            ),
            runtime_dir=str(run_data.get("runtime_dir", "data/runtime")).strip()
            or "data/runtime",
        ),
        chunk_max_chars=_positive_int(processing.get("chunk_max_chars"), 1200),
    )


def _read_config_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        loaded = json.loads(text)
    elif path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise ImportError("Install PyYAML to read YAML config files.") from exc
        loaded = yaml.safe_load(text)
    else:
        raise ValueError("Config file must be .json, .yaml, or .yml.")
    return loaded or {}


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Config section '{key}' must be an object.")
    return value


def _string_list(value: Any, default: list[str] | None = None) -> list[str]:
    if value is None:
        return list(default or [])
    if not isinstance(value, list):
        raise ValueError("Expected a list of strings in config.")
    return [str(item).strip() for item in value if str(item).strip()]


def _youtube_videos(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Config field 'youtube.videos' must be a list.")

    videos: list[str] = []
    for item in value:
        if isinstance(item, str):
            url = item.strip()
        elif isinstance(item, dict):
            url = str(item.get("url", "")).strip()
        else:
            raise ValueError("YouTube video entries must be strings or objects with 'url'.")
        if url:
            videos.append(url)
    return videos


def _transcript_files(value: Any, base_dir: Path) -> list[TranscriptFileConfig]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Config field 'youtube.transcript_files' must be a list.")

    transcript_files: list[TranscriptFileConfig] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Transcript file entries must be objects.")

        path = str(item.get("path", "")).strip()
        source_url = str(item.get("source_url", "")).strip()
        title = str(item.get("title", "")).strip()
        if not path or not source_url or not title:
            raise ValueError(
                "Transcript file entries require 'path', 'source_url', and 'title'."
            )

        resolved_path = Path(path)
        if not resolved_path.is_absolute():
            resolved_path = base_dir / resolved_path

        transcript_files.append(
            TranscriptFileConfig(
                path=str(resolved_path),
                source_url=source_url,
                title=title,
                channel=str(item.get("channel", "")).strip(),
                published_date=str(item.get("published_date", "")).strip(),
            )
        )

    return transcript_files


def _positive_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Expected a positive integer, got {value!r}.") from exc
    if parsed <= 0:
        raise ValueError(f"Expected a positive integer, got {value!r}.")
    return parsed


def _non_negative_float(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Expected a non-negative number, got {value!r}.") from exc
    if parsed < 0:
        raise ValueError(f"Expected a non-negative number, got {value!r}.")
    return parsed


def _non_negative_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Expected a non-negative integer, got {value!r}.") from exc
    if parsed < 0:
        raise ValueError(f"Expected a non-negative integer, got {value!r}.")
    return parsed
