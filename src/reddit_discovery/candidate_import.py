from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from src.processing.cleaner import clean_text, clean_title
from src.reddit_discovery.constants import SUPPORTED_SUBREDDIT
from src.reddit_discovery.discovery import absolute_reddit_url, canonicalize_url
from src.reddit_discovery.models import DiscoveryError, RedditCandidate
from src.time_utils import utc_now


@dataclass(slots=True)
class CandidateImportResult:
    candidates: list[RedditCandidate] = field(default_factory=list)
    errors: list[DiscoveryError] = field(default_factory=list)


def load_candidate_file(
    path: str | Path,
    *,
    retrieved_at: str | None = None,
) -> CandidateImportResult:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Reddit candidate file not found: {source}")

    records, parse_errors = _read_records(source)
    result = CandidateImportResult(errors=parse_errors)
    import_time = retrieved_at or utc_now()
    for index, record in enumerate(records, start=1):
        try:
            candidate = _candidate_from_mapping(
                record,
                source_name=source.name,
                record_index=index,
                retrieved_at=import_time,
            )
        except (TypeError, ValueError) as exc:
            result.errors.append(
                DiscoveryError(
                    route=f"import:{source.name}:{index}",
                    error_type=type(exc).__name__,
                    message=f"Skipped malformed imported Reddit record: {exc}",
                    retryable=False,
                    attempts=1,
                )
            )
            continue
        result.candidates.append(candidate)
    return result


def _read_records(source: Path) -> tuple[list[Mapping[str, Any]], list[DiscoveryError]]:
    if source.suffix.casefold() == ".jsonl":
        return _read_jsonl(source)
    if source.suffix.casefold() != ".json":
        raise ValueError("Offline Reddit candidates must use .json or .jsonl.")

    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not parse Reddit candidate JSON: {type(exc).__name__}.") from exc

    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, Mapping):
        values = next(
            (
                payload[key]
                for key in ("raw_candidates", "candidates", "items")
                if isinstance(payload.get(key), list)
            ),
            None,
        )
        if values is None and _looks_like_candidate(payload):
            values = [payload]
        if values is None:
            raise ValueError(
                "Reddit candidate JSON must be a list or contain raw_candidates/candidates/items."
            )
    else:
        raise ValueError("Reddit candidate JSON must contain an object or list.")

    records = [item for item in values if isinstance(item, Mapping)]
    errors = [
        DiscoveryError(
            route=f"import:{source.name}:{index}",
            error_type="TypeError",
            message="Skipped imported Reddit record because it is not an object.",
            retryable=False,
            attempts=1,
        )
        for index, item in enumerate(values, start=1)
        if not isinstance(item, Mapping)
    ]
    return records, errors


def _read_jsonl(source: Path) -> tuple[list[Mapping[str, Any]], list[DiscoveryError]]:
    records: list[Mapping[str, Any]] = []
    errors: list[DiscoveryError] = []
    try:
        lines = source.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Could not read Reddit candidate JSONL: {type(exc).__name__}.") from exc

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            errors.append(
                DiscoveryError(
                    route=f"import:{source.name}:line-{line_number}",
                    error_type="JSONDecodeError",
                    message="Skipped malformed Reddit JSONL record.",
                    retryable=False,
                    attempts=1,
                )
            )
            continue
        if not isinstance(value, Mapping):
            errors.append(
                DiscoveryError(
                    route=f"import:{source.name}:line-{line_number}",
                    error_type="TypeError",
                    message="Skipped Reddit JSONL record because it is not an object.",
                    retryable=False,
                    attempts=1,
                )
            )
            continue
        records.append(value)
    return records, errors


def _candidate_from_mapping(
    value: Mapping[str, Any],
    *,
    source_name: str,
    record_index: int,
    retrieved_at: str,
) -> RedditCandidate:
    data = dict(value)
    post_id = _text(data.get("reddit_post_id", data.get("id")))
    if not post_id:
        raise ValueError("reddit_post_id is required.")

    subreddit = _subreddit_name(data.get("subreddit"))
    if subreddit.casefold() != SUPPORTED_SUBREDDIT.casefold():
        raise ValueError(f"subreddit must be {SUPPORTED_SUBREDDIT}.")

    raw_permalink = _text(data.get("permalink"))
    raw_canonical = _text(data.get("canonical_url"))
    raw_url = _text(data.get("url"))
    permalink = absolute_reddit_url(raw_permalink)
    if not permalink and _is_reddit_url(raw_canonical):
        permalink = canonicalize_url(raw_canonical)
    if not permalink and _is_reddit_url(raw_url):
        permalink = canonicalize_url(raw_url)
    if not permalink:
        raise ValueError("a Reddit permalink or canonical Reddit URL is required.")

    external_url = _text(data.get("external_url"))
    if not external_url and raw_url and not _is_reddit_url(raw_url):
        external_url = canonicalize_url(raw_url)
    upstream_provenance = data.get("provenance")
    provenance = dict(upstream_provenance) if isinstance(upstream_provenance, Mapping) else {}
    provenance.update(
        {
            "source_file": source_name,
            "record_index": record_index,
        }
    )
    normalized = {
        **data,
        "reddit_post_id": post_id,
        "fullname": _text(data.get("fullname")) or f"t3_{post_id}",
        "subreddit": SUPPORTED_SUBREDDIT,
        "title": clean_title(_text(data.get("title"))),
        "selftext": clean_text(
            _text(
                data.get(
                    "selftext",
                    data.get("cleaned_text", data.get("body_text", data.get("body"))),
                )
            )
        ),
        "canonical_url": permalink,
        "permalink": permalink,
        "author_name": data.get("author_name", data.get("author")),
        "retrieved_at": _text(data.get("retrieved_at")) or retrieved_at,
        "num_comments": data.get("num_comments", data.get("comment_count")),
        "link_flair_text": data.get("link_flair_text", data.get("flair")),
        "external_url": external_url or None,
        "acquisition_method": _text(data.get("acquisition_method")) or "imported_json",
        "provenance": provenance,
        "discovered_by": data.get("discovered_by") or [f"import:{source_name}"],
    }
    candidate = RedditCandidate.from_dict(normalized)
    candidate.refresh_content_hash()
    return candidate


def _subreddit_name(value: Any) -> str:
    if isinstance(value, Mapping):
        return _text(value.get("display_name"))
    return _text(value)


def _looks_like_candidate(value: Mapping[str, Any]) -> bool:
    return bool(value.get("reddit_post_id") or value.get("id"))


def _is_reddit_url(value: str) -> bool:
    host = (urlparse(value).hostname or "").casefold()
    return host == "reddit.com" or host.endswith(".reddit.com") or host == "redd.it"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()
