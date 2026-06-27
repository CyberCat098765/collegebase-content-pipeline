from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Chunk:
    chunk_id: str
    text: str
    source_url: str
    citation_label: str
    start_timestamp: str = ""
    end_timestamp: str = ""
    topic_tags: list[str] = field(default_factory=list)
    audience: list[str] = field(default_factory=list)
    content_use: str = "chatbot_answer"
    usefulness_score: int = 1
    why_useful: str = ""
    admissions_relevance_score: int = 1
    admissions_topics: list[str] = field(default_factory=list)
    drop_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SourceItem:
    source_type: str
    source_name: str
    title: str
    url: str
    author_or_channel: str
    published_date: str
    collected_at: str
    raw_text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    chunks: list[Chunk] = field(default_factory=list)
    admissions_relevance_score: int = 1
    admissions_topics: list[str] = field(default_factory=list)
    drop_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        visible_metadata = {
            key: value for key, value in self.metadata.items() if not key.startswith("_")
        }
        return {
            "source_type": self.source_type,
            "source_name": self.source_name,
            "title": self.title,
            "url": self.url,
            "author_or_channel": self.author_or_channel,
            "published_date": self.published_date,
            "collected_at": self.collected_at,
            "raw_text": self.raw_text,
            "admissions_relevance_score": self.admissions_relevance_score,
            "admissions_topics": self.admissions_topics,
            "drop_reason": self.drop_reason,
            "metadata": visible_metadata,
            "chunks": [chunk.to_dict() for chunk in self.chunks],
        }


@dataclass(slots=True)
class PipelineError:
    source_type: str
    source: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class PipelineOutput:
    pipeline_version: str
    collected_at: str
    run_summary: dict[str, Any] = field(default_factory=dict)
    items: list[SourceItem] = field(default_factory=list)
    errors: list[PipelineError] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_version": self.pipeline_version,
            "collected_at": self.collected_at,
            "run_summary": self.run_summary,
            "items": [item.to_dict() for item in self.items],
            "errors": [error.to_dict() for error in self.errors],
        }
