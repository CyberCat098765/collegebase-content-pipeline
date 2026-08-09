from __future__ import annotations

import copy
import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

from src.reddit_discovery.storage import atomic_write_json


REGISTRY_SCHEMA_VERSION = 1
REQUIRED_REGISTRY_FIELDS = (
    "reddit_post_id",
    "canonical_url",
    "content_hash",
    "first_seen_at",
    "last_seen_at",
    "last_processed_at",
    "processing_status",
    "accepted",
    "final_usefulness_score",
    "pipeline_version",
    "model_name",
    "prompt_version",
    "output_resource_id",
    "failure_reason",
)

_INCOMPLETE_STATUSES = {"", "pending", "processing", "failed", "error"}
_TERMINAL_STATUSES = {"accepted", "human_review", "rejected", "processed", "completed"}


@dataclass(frozen=True, slots=True)
class ProcessingDecision:
    reddit_post_id: str
    should_process: bool
    reason: str
    previous_entry: dict[str, Any] | None = None
    carried_record: dict[str, Any] | None = None

    @property
    def should_skip(self) -> bool:
        return not self.should_process


class SourceRegistry:
    """In-memory per-post registry; persistence is always an explicit call."""

    def __init__(
        self,
        posts: Mapping[str, Mapping[str, Any]] | None = None,
        *,
        load_errors: Iterable[str] = (),
    ) -> None:
        self.posts: dict[str, dict[str, Any]] = {}
        self.load_errors = list(load_errors)
        for key, value in (posts or {}).items():
            if not isinstance(value, Mapping):
                self.load_errors.append(f"Ignored malformed registry entry: {key}")
                continue
            post_id = str(value.get("reddit_post_id", key)).strip()
            if not post_id:
                self.load_errors.append("Ignored registry entry with no Reddit post ID")
                continue
            self.posts[post_id] = _normalize_entry(value, post_id)

    @classmethod
    def load(cls, path: str | Path) -> SourceRegistry:
        source = Path(path)
        if not source.exists():
            return cls()
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return cls(load_errors=(f"Could not load source registry {source}: {exc}",))

        if not isinstance(payload, Mapping):
            return cls(load_errors=(f"Source registry {source} is not a JSON object",))

        posts = payload.get("posts")
        if posts is None and _looks_like_post_map(payload):
            posts = {
                key: value
                for key, value in payload.items()
                if key not in {"schema_version", "updated_at"}
            }
        if not isinstance(posts, Mapping):
            return cls(load_errors=(f"Source registry {source} has no valid posts map",))
        return cls(posts)

    def observe(
        self,
        candidate: Mapping[str, Any] | object,
        *,
        seen_at: str,
        pipeline_version: str,
        model_name: str = "",
        prompt_version: str = "",
        scoring_version: str = "",
        force: bool = False,
    ) -> ProcessingDecision:
        """Record a sighting and decide whether this post needs processing."""

        item = _as_mapping(candidate)
        post_id = _required_text(item, "reddit_post_id")
        canonical_url = _required_text(item, "canonical_url")
        content_hash = _required_text(item, "content_hash")
        previous = copy.deepcopy(self.posts.get(post_id))
        reason = reprocessing_reason(
            previous,
            content_hash=content_hash,
            pipeline_version=pipeline_version,
            model_name=model_name,
            prompt_version=prompt_version,
            scoring_version=scoring_version,
            force=force,
        )
        should_process = reason != "unchanged"

        entry = copy.deepcopy(previous) if previous is not None else {}
        entry.update(
            {
                "reddit_post_id": post_id,
                "canonical_url": canonical_url,
                "content_hash": content_hash,
                "first_seen_at": entry.get("first_seen_at") or seen_at,
                "last_seen_at": seen_at,
                "pipeline_version": pipeline_version,
                "model_name": model_name,
                "prompt_version": prompt_version,
            }
        )
        if scoring_version or "scoring_version" in entry:
            entry["scoring_version"] = scoring_version
        if should_process:
            entry.update(
                {
                    "processing_status": "pending",
                    "accepted": False,
                    "final_usefulness_score": None,
                    "output_resource_id": None,
                    "failure_reason": None,
                }
            )
        self.posts[post_id] = _normalize_entry(entry, post_id)

        carried = _record_data(previous) if not should_process else None
        return ProcessingDecision(
            reddit_post_id=post_id,
            should_process=should_process,
            reason=reason,
            previous_entry=previous,
            carried_record=carried,
        )

    def record_result(
        self,
        reddit_post_id: str,
        *,
        processed_at: str,
        processing_status: str,
        accepted: bool,
        final_usefulness_score: int | float | None,
        output_resource_id: str | None = None,
        failure_reason: str | None = None,
        record_data: Mapping[str, Any] | object | None = None,
        record_kind: str | None = None,
    ) -> dict[str, Any]:
        post_id = reddit_post_id.strip()
        if not post_id or post_id not in self.posts:
            raise KeyError(f"Reddit post has not been observed: {reddit_post_id!r}")
        if final_usefulness_score is not None and not 0 <= final_usefulness_score <= 100:
            raise ValueError("final_usefulness_score must be between 0 and 100")

        entry = self.posts[post_id]
        entry.update(
            {
                "last_processed_at": processed_at,
                "processing_status": processing_status,
                "accepted": bool(accepted),
                "final_usefulness_score": final_usefulness_score,
                "output_resource_id": output_resource_id,
                "failure_reason": failure_reason,
            }
        )
        if record_data is not None:
            entry["record_data"] = copy.deepcopy(_as_mapping(record_data))
        elif processing_status in _TERMINAL_STATUSES:
            entry.pop("record_data", None)
        if record_kind is not None:
            entry["record_kind"] = record_kind
        elif processing_status in {"accepted", "human_review", "rejected"}:
            entry["record_kind"] = processing_status
        self.posts[post_id] = _normalize_entry(entry, post_id)
        return copy.deepcopy(self.posts[post_id])

    def record_failure(
        self,
        reddit_post_id: str,
        *,
        processed_at: str,
        failure_reason: str,
    ) -> dict[str, Any]:
        return self.record_result(
            reddit_post_id,
            processed_at=processed_at,
            processing_status="failed",
            accepted=False,
            final_usefulness_score=None,
            output_resource_id=None,
            failure_reason=failure_reason,
        )

    def get(self, reddit_post_id: str) -> dict[str, Any] | None:
        entry = self.posts.get(reddit_post_id)
        return copy.deepcopy(entry) if entry is not None else None

    def carry_forward(self, reddit_post_id: str) -> dict[str, Any] | None:
        entry = self.posts.get(reddit_post_id)
        if not _entry_is_complete(entry):
            return None
        return _record_data(entry)

    def carried_records(self, record_kind: str | None = None) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for post_id in sorted(self.posts):
            entry = self.posts[post_id]
            if record_kind is not None and entry.get("record_kind") != record_kind:
                continue
            record = self.carry_forward(post_id)
            if record is not None:
                records.append(record)
        return records

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "posts": {key: copy.deepcopy(self.posts[key]) for key in sorted(self.posts)},
        }


def reprocessing_reason(
    previous: Mapping[str, Any] | None,
    *,
    content_hash: str,
    pipeline_version: str,
    model_name: str = "",
    prompt_version: str = "",
    scoring_version: str = "",
    force: bool = False,
) -> str:
    if force:
        return "force"
    if previous is None:
        return "new_post"
    if str(previous.get("content_hash", "")) != content_hash:
        return "content_changed"
    if str(previous.get("pipeline_version", "")) != pipeline_version:
        return "pipeline_version_changed"
    if scoring_version and str(previous.get("scoring_version", "")) != scoring_version:
        return "scoring_version_changed"
    if str(previous.get("model_name", "")) != model_name:
        return "model_name_changed"
    if str(previous.get("prompt_version", "")) != prompt_version:
        return "prompt_version_changed"
    if not _entry_is_complete(previous):
        return "previous_processing_incomplete"
    return "unchanged"


def load_source_registry(path: str | Path) -> SourceRegistry:
    return SourceRegistry.load(path)


def save_source_registry(registry: SourceRegistry | Mapping[str, Any], path: str | Path) -> Path:
    payload = registry.to_dict() if isinstance(registry, SourceRegistry) else copy.deepcopy(registry)
    return atomic_write_json(path, payload, sort_keys=True)


def _normalize_entry(value: Mapping[str, Any], post_id: str) -> dict[str, Any]:
    entry = copy.deepcopy(dict(value))
    defaults: dict[str, Any] = {
        "reddit_post_id": post_id,
        "canonical_url": "",
        "content_hash": "",
        "first_seen_at": "",
        "last_seen_at": "",
        "last_processed_at": None,
        "processing_status": "pending",
        "accepted": False,
        "final_usefulness_score": None,
        "pipeline_version": "",
        "model_name": "",
        "prompt_version": "",
        "output_resource_id": None,
        "failure_reason": None,
    }
    for field, default in defaults.items():
        entry.setdefault(field, default)
    return entry


def _entry_is_complete(entry: Mapping[str, Any] | None) -> bool:
    if not entry or not entry.get("last_processed_at"):
        return False
    status = str(entry.get("processing_status", "")).lower()
    return status in _TERMINAL_STATUSES or status not in _INCOMPLETE_STATUSES


def _record_data(entry: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not entry or not isinstance(entry.get("record_data"), Mapping):
        return None
    return copy.deepcopy(dict(entry["record_data"]))


def _as_mapping(value: Mapping[str, Any] | object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        if isinstance(result, Mapping):
            return dict(result)
    if is_dataclass(value):
        result = asdict(value)
        if isinstance(result, dict):
            return result
    raise TypeError(f"Expected a mapping or serializable record, got {type(value).__name__}")


def _required_text(value: Mapping[str, Any], field: str) -> str:
    result = str(value.get(field, "")).strip()
    if not result:
        raise ValueError(f"Candidate is missing required field: {field}")
    return result


def _looks_like_post_map(value: Mapping[str, Any]) -> bool:
    if not value:
        return True
    ignored = {"schema_version", "updated_at"}
    records = [item for key, item in value.items() if key not in ignored]
    return bool(records) and all(isinstance(item, Mapping) for item in records)
