from __future__ import annotations

import hashlib
import logging
from typing import Iterable

from src.models import Chunk, PipelineError, SourceItem
from src.processing.admissions_filter import (
    MIN_ADMISSIONS_RELEVANCE_SCORE,
    evaluate_admissions_relevance,
)
from src.processing.chunker import TextChunk, chunk_text, chunk_transcript_segments
from src.processing.cleaner import clean_text
from src.processing.scorer import score_chunk
from src.processing.tagger import (
    infer_audience_tags,
    infer_content_use,
    infer_topic_tags,
)

LOGGER = logging.getLogger(__name__)
MIN_USEFUL_CHUNK_WORDS = 40


def process_items(
    items: list[SourceItem],
    max_chars: int,
) -> tuple[list[SourceItem], list[PipelineError], dict[str, int]]:
    processed: list[SourceItem] = []
    errors: list[PipelineError] = []
    stats = empty_processing_stats()

    for item in items:
        item.raw_text = clean_text(item.raw_text)
        if not item.raw_text:
            LOGGER.warning("Skipping empty item from %s", item.url)
            continue

        text_chunks = _chunk_item(item, max_chars=max_chars)
        stats["chunks_before_admissions_filter"] += len(text_chunks)
        kept_chunks: list[Chunk] = []
        dropped_chunks = 0
        dropped_details: list[dict[str, object]] = []

        for index, text_chunk in enumerate(text_chunks, start=1):
            chunk = _build_chunk(item=item, text_chunk=text_chunk, index=index)
            relevance = evaluate_admissions_relevance(
                text=chunk.text,
                source_type=item.source_type,
                title=item.title,
                metadata=item.metadata,
            )
            chunk.admissions_relevance_score = relevance.score
            chunk.admissions_topics = relevance.topics
            chunk.drop_reason = relevance.drop_reason

            if relevance.score < MIN_ADMISSIONS_RELEVANCE_SCORE:
                dropped_chunks += 1
                dropped_details.append(_drop_detail(chunk, relevance.drop_reason))
                continue
            if _word_count(chunk.text) < MIN_USEFUL_CHUNK_WORDS and item.source_type != "official":
                dropped_chunks += 1
                dropped_details.append(_drop_detail(chunk, "Chunk was too short for citation or content brief use."))
                continue
            kept_chunks.append(chunk)

        stats["chunks_dropped_by_admissions_filter"] += dropped_chunks
        total_chunks = len(kept_chunks) + dropped_chunks
        drop_reasons = _unique(
            str(detail["drop_reason"])
            for detail in dropped_details
            if str(detail.get("drop_reason", ""))
        )
        if not kept_chunks:
            stats["sources_dropped_by_admissions_filter"] += 1
            errors.append(
                PipelineError(
                    item.source_type,
                    item.url,
                    (
                        "Dropped by admissions relevance filter: all chunks were below "
                        f"score 3. Reasons: {', '.join(drop_reasons) or 'not recorded'}."
                    ),
                )
            )
            continue
        if dropped_chunks > len(kept_chunks):
            stats["sources_dropped_by_admissions_filter"] += 1
            errors.append(
                PipelineError(
                    item.source_type,
                    item.url,
                    (
                        "Dropped by admissions relevance filter: most chunks were below "
                        f"score 3 ({dropped_chunks}/{total_chunks} dropped). "
                        f"Reasons: {', '.join(drop_reasons) or 'not recorded'}."
                    ),
                )
            )
            continue

        item.chunks = kept_chunks
        item.admissions_relevance_score = max(
            chunk.admissions_relevance_score for chunk in kept_chunks
        )
        item.admissions_topics = _unique(
            topic for chunk in kept_chunks for topic in chunk.admissions_topics
        )
        item.drop_reason = ""
        item.metadata["admissions_filter"] = {
            "min_chunk_score": MIN_ADMISSIONS_RELEVANCE_SCORE,
            "chunks_before_filter": total_chunks,
            "chunks_kept": len(kept_chunks),
            "chunks_dropped": dropped_chunks,
            "drop_reasons": drop_reasons,
            "dropped_chunk_previews": dropped_details[:5],
        }
        processed.append(item)

    return processed, errors, stats


def empty_processing_stats() -> dict[str, int]:
    return {
        "chunks_before_admissions_filter": 0,
        "chunks_dropped_by_admissions_filter": 0,
        "sources_dropped_by_admissions_filter": 0,
    }


def merge_processing_stats(total: dict[str, int], update: dict[str, int]) -> None:
    for key, value in update.items():
        total[key] = total.get(key, 0) + value


def _chunk_item(item: SourceItem, max_chars: int) -> list[TextChunk]:
    if item.source_type == "youtube":
        transcript_segments = item.metadata.get("_transcript_segments")
        if isinstance(transcript_segments, list):
            return chunk_transcript_segments(transcript_segments, max_chars=max_chars)
    return chunk_text(item.raw_text, max_chars=max_chars)


def _build_chunk(item: SourceItem, text_chunk: TextChunk, index: int) -> Chunk:
    text = clean_text(text_chunk.text)
    topic_tags = infer_topic_tags(text)
    audience = infer_audience_tags(text)
    content_use = infer_content_use(text, item.source_type, topic_tags)
    usefulness_score, why_useful = score_chunk(
        text=text,
        source_type=item.source_type,
        metadata=item.metadata,
        topic_tags=topic_tags,
    )

    return Chunk(
        chunk_id=_chunk_id(item.url, index, text),
        text=text,
        source_url=item.url,
        citation_label=_citation_label(item, text_chunk),
        start_timestamp=text_chunk.start_timestamp,
        end_timestamp=text_chunk.end_timestamp,
        topic_tags=topic_tags,
        audience=audience,
        content_use=content_use,
        usefulness_score=usefulness_score,
        why_useful=why_useful,
    )


def _citation_label(item: SourceItem, text_chunk: TextChunk) -> str:
    title = item.title[:90].rstrip()
    if item.source_type == "reddit":
        return f"Reddit, {item.source_name}: {title}"
    if item.source_type == "youtube" and text_chunk.start_timestamp:
        return f"YouTube, {item.author_or_channel or item.source_name}: {title} at {text_chunk.start_timestamp}"
    if item.source_type == "official":
        return f"College Scorecard: {title}"
    return f"{item.source_name}: {title}"


def _chunk_id(source_url: str, index: int, text: str) -> str:
    digest = hashlib.sha1(f"{source_url}:{index}:{text[:200]}".encode("utf-8")).hexdigest()
    return f"chunk_{digest[:12]}"


def _drop_detail(chunk: Chunk, drop_reason: str) -> dict[str, object]:
    return {
        "admissions_relevance_score": chunk.admissions_relevance_score,
        "admissions_topics": chunk.admissions_topics,
        "drop_reason": drop_reason,
        "text_preview": _preview(chunk.text),
    }


def _word_count(text: str) -> int:
    return len(text.split())


def _preview(text: str, max_chars: int = 220) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars].rsplit(" ", 1)[0] + "..."


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            unique_values.append(value)
    return unique_values
