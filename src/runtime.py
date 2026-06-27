from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

from src.config import PipelineConfig


@dataclass(slots=True)
class SourceJob:
    job_id: str
    source_type: str
    url: str
    status: str
    attempts: int
    error: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceJob:
        return cls(
            job_id=str(data.get("job_id", "")),
            source_type=str(data.get("source_type", "")),
            url=str(data.get("url", "")),
            status=str(data.get("status", "pending")),
            attempts=int(data.get("attempts", 0) or 0),
            error=str(data.get("error", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


def build_source_jobs(config: PipelineConfig, created_at: str) -> list[SourceJob]:
    jobs: list[SourceJob] = []

    for url in config.articles.urls:
        jobs.append(_job("article", url, created_at))

    for url in config.youtube.videos:
        jobs.append(_job("youtube", url, created_at))

    for transcript in config.youtube.transcript_files:
        jobs.append(_job("transcript_file", transcript.source_url, created_at))

    for url in config.reddit.urls:
        jobs.append(_job("reddit", url, created_at))

    for subreddit in config.reddit.subreddits:
        for keyword in config.reddit.keywords:
            jobs.append(_job("reddit", f"reddit://search/{subreddit}?q={quote(keyword)}", created_at))

    if config.official.college_scorecard.enabled:
        for school in config.official.college_scorecard.schools:
            jobs.append(_job("official", school, created_at))

    return jobs


def load_jobs(runtime_dir: str | Path) -> list[SourceJob]:
    path = Path(runtime_dir) / "jobs.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [SourceJob.from_dict(item) for item in data.get("jobs", [])]


def save_jobs(jobs: list[SourceJob], runtime_dir: str | Path) -> None:
    path = Path(runtime_dir) / "jobs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"jobs": [job.to_dict() for job in jobs]}, indent=2) + "\n",
        encoding="utf-8",
    )


def save_checkpoint(
    jobs: list[SourceJob],
    runtime_dir: str | Path,
    checkpoint: dict[str, Any],
) -> None:
    path = Path(runtime_dir) / "checkpoint.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({**checkpoint, "jobs": [job.to_dict() for job in jobs]}, indent=2) + "\n",
        encoding="utf-8",
    )


def load_source_registry(runtime_dir: str | Path) -> dict[str, Any]:
    path = Path(runtime_dir) / "source_registry.json"
    if not path.exists():
        return {"sources": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"sources": {}}
    if not isinstance(data, dict) or not isinstance(data.get("sources"), dict):
        return {"sources": {}}
    return data


def save_source_registry(registry: dict[str, Any], runtime_dir: str | Path) -> None:
    path = Path(runtime_dir) / "source_registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def source_registry_key(job: SourceJob) -> str:
    source = _normalized_source(job.url)
    digest = hashlib.sha1(f"{job.source_type}:{source}".encode("utf-8")).hexdigest()
    return f"{job.source_type}:{digest[:16]}"


def source_succeeded_before(registry: dict[str, Any], job: SourceJob) -> bool:
    sources = registry.get("sources", {})
    if not isinstance(sources, dict):
        return False
    entry = sources.get(source_registry_key(job), {})
    return isinstance(entry, dict) and entry.get("status") == "succeeded"


def update_source_registry(
    registry: dict[str, Any],
    job: SourceJob,
    updated_at: str,
) -> None:
    sources = registry.setdefault("sources", {})
    if not isinstance(sources, dict):
        registry["sources"] = {}
        sources = registry["sources"]

    key = source_registry_key(job)
    previous = sources.get(key, {}) if isinstance(sources.get(key), dict) else {}
    entry = {
        "source_type": job.source_type,
        "url": job.url,
        "normalized_source": _normalized_source(job.url),
        "status": job.status,
        "last_error": job.error,
        "attempts": job.attempts,
        "updated_at": updated_at,
    }
    if job.status == "succeeded":
        entry["last_succeeded_at"] = updated_at
    elif previous.get("last_succeeded_at"):
        entry["last_succeeded_at"] = previous["last_succeeded_at"]
    sources[key] = entry


def reset_jobs(jobs: list[SourceJob], updated_at: str) -> list[SourceJob]:
    for job in jobs:
        job.status = "pending"
        job.attempts = 0
        job.error = ""
        job.updated_at = updated_at
    return jobs


def credential_skips(config: PipelineConfig, jobs: list[SourceJob]) -> dict[str, str]:
    skips: dict[str, str] = {}

    if any(job.source_type == "reddit" for job in jobs) and not _reddit_credentials_present():
        skips["reddit"] = (
            "Missing Reddit API credentials. Set REDDIT_CLIENT_ID, "
            "REDDIT_CLIENT_SECRET, and REDDIT_USER_AGENT to enable Reddit collection."
        )

    if any(job.source_type == "official" for job in jobs):
        api_key_env = config.official.college_scorecard.api_key_env
        if not os.getenv(api_key_env, "").strip():
            skips["official"] = f"{api_key_env} is not set; College Scorecard will skip."

    return skips


def invalid_job_sources(jobs: list[SourceJob]) -> list[str]:
    invalid: list[str] = []
    for job in jobs:
        if job.source_type in {"article", "youtube", "transcript_file"}:
            if not _is_http_url(job.url):
                invalid.append(f"{job.source_type}: {job.url}")
        elif job.source_type == "reddit":
            if not (job.url.startswith("reddit://search/") or _is_http_url(job.url)):
                invalid.append(f"reddit: {job.url}")
        elif job.source_type == "official":
            if not job.url.strip():
                invalid.append("official: empty school name")
        else:
            invalid.append(f"unknown source type: {job.source_type}")
    return invalid


def job_counts(jobs: list[SourceJob]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for job in jobs:
        counts[job.source_type] = counts.get(job.source_type, 0) + 1
    return counts


def _job(source_type: str, url: str, created_at: str) -> SourceJob:
    return SourceJob(
        job_id=_job_id(source_type, url),
        source_type=source_type,
        url=url,
        status="pending",
        attempts=0,
        error="",
        created_at=created_at,
        updated_at=created_at,
    )


def _job_id(source_type: str, url: str) -> str:
    digest = hashlib.sha1(f"{source_type}:{url}".encode("utf-8")).hexdigest()
    return f"job_{digest[:12]}"


def _is_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _normalized_source(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        path = parsed.path.rstrip("/") or "/"
        return urlunparse(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                path,
                "",
                parsed.query,
                "",
            )
        )
    return " ".join(value.lower().split())


def _reddit_credentials_present() -> bool:
    return all(
        os.getenv(name, "").strip()
        for name in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT")
    )
