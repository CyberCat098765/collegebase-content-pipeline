from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class TextChunk:
    text: str
    start_timestamp: str = ""
    end_timestamp: str = ""


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def chunk_text(text: str, max_chars: int = 1200) -> list[TextChunk]:
    if not text.strip():
        return []

    pieces = _split_to_pieces(text, max_chars)
    chunks: list[str] = []
    current = ""

    for piece in pieces:
        candidate = f"{current}\n\n{piece}".strip() if current else piece
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = piece

    if current:
        chunks.append(current)

    return [TextChunk(text=chunk) for chunk in chunks]


def chunk_transcript_segments(
    segments: list[dict[str, object]], max_chars: int = 1200
) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    current_text: list[str] = []
    current_start = ""
    current_end = ""

    for segment in segments:
        text = str(segment.get("text", "")).strip()
        if not text:
            continue

        start = _format_timestamp(segment.get("start"))
        end = _format_timestamp(segment.get("end"))
        candidate = " ".join([*current_text, text]).strip()

        if candidate and len(candidate) > max_chars and current_text:
            chunks.append(
                TextChunk(
                    text=" ".join(current_text).strip(),
                    start_timestamp=current_start,
                    end_timestamp=current_end,
                )
            )
            current_text = [text]
            current_start = start
            current_end = end
            continue

        if not current_text:
            current_start = start
        current_text.append(text)
        current_end = end

    if current_text:
        chunks.append(
            TextChunk(
                text=" ".join(current_text).strip(),
                start_timestamp=current_start,
                end_timestamp=current_end,
            )
        )

    return chunks


def _split_to_pieces(text: str, max_chars: int) -> list[str]:
    paragraphs = [paragraph.strip() for paragraph in text.split("\n") if paragraph.strip()]
    pieces: list[str] = []

    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            pieces.append(paragraph)
            continue

        sentences = _SENTENCE_RE.split(paragraph)
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) <= max_chars:
                pieces.append(sentence)
            else:
                pieces.extend(_split_long_text(sentence, max_chars))

    return pieces


def _split_long_text(text: str, max_chars: int) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    current: list[str] = []

    for word in words:
        candidate = " ".join([*current, word])
        if len(candidate) <= max_chars:
            current.append(word)
            continue
        if current:
            chunks.append(" ".join(current))
        current = [word]

    if current:
        chunks.append(" ".join(current))
    return chunks


def _format_timestamp(value: object) -> str:
    if value is None or value == "":
        return ""
    try:
        total_seconds = max(0, int(float(value)))
    except (TypeError, ValueError):
        return ""

    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
