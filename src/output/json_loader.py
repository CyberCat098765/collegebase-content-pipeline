from __future__ import annotations

import json
from pathlib import Path

from src.models import Chunk, PipelineError, SourceItem


def load_pipeline_parts(path: str | Path) -> tuple[list[SourceItem], list[PipelineError]]:
    output_path = Path(path)
    if not output_path.exists():
        return [], []

    try:
        data = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], []

    items = [_source_item_from_dict(item) for item in data.get("items", [])]
    errors = [_pipeline_error_from_dict(error) for error in data.get("errors", [])]
    return items, errors


def _source_item_from_dict(data: dict[str, object]) -> SourceItem:
    return SourceItem(
        source_type=str(data.get("source_type", "")),
        source_name=str(data.get("source_name", "")),
        title=str(data.get("title", "")),
        url=str(data.get("url", "")),
        author_or_channel=str(data.get("author_or_channel", "")),
        published_date=str(data.get("published_date", "")),
        collected_at=str(data.get("collected_at", "")),
        raw_text=str(data.get("raw_text", "")),
        metadata=dict(data.get("metadata", {})) if isinstance(data.get("metadata"), dict) else {},
        chunks=[_chunk_from_dict(chunk) for chunk in data.get("chunks", []) if isinstance(chunk, dict)],
        admissions_relevance_score=int(data.get("admissions_relevance_score", 1) or 1),
        admissions_topics=_list_field(data, "admissions_topics"),
        drop_reason=str(data.get("drop_reason", "")),
    )


def _chunk_from_dict(data: dict[str, object]) -> Chunk:
    return Chunk(
        chunk_id=str(data.get("chunk_id", "")),
        text=str(data.get("text", "")),
        source_url=str(data.get("source_url", "")),
        citation_label=str(data.get("citation_label", "")),
        start_timestamp=str(data.get("start_timestamp", "")),
        end_timestamp=str(data.get("end_timestamp", "")),
        topic_tags=_list_field(data, "topic_tags"),
        audience=_list_field(data, "audience"),
        content_use=str(data.get("content_use", "chatbot_answer")),
        usefulness_score=int(data.get("usefulness_score", 1) or 1),
        why_useful=str(data.get("why_useful", "")),
        admissions_relevance_score=int(data.get("admissions_relevance_score", 1) or 1),
        admissions_topics=_list_field(data, "admissions_topics"),
        drop_reason=str(data.get("drop_reason", "")),
    )


def _pipeline_error_from_dict(data: dict[str, object]) -> PipelineError:
    return PipelineError(
        source_type=str(data.get("source_type", "")),
        source=str(data.get("source", "")),
        message=str(data.get("message", "")),
    )


def _list_field(data: dict[str, object], key: str) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]
