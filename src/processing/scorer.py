from __future__ import annotations

from typing import Any


ADMISSIONS_TOPICS = {"general_admissions"}


def score_chunk(
    text: str,
    source_type: str,
    metadata: dict[str, Any],
    topic_tags: list[str],
) -> tuple[int, str]:
    score = 1
    reasons: list[str] = []
    word_count = len(text.split())

    if word_count >= 80:
        score += 1
        reasons.append("substantive length")
    if word_count >= 180:
        score += 1
        reasons.append("detailed context")
    if topic_tags and set(topic_tags) != ADMISSIONS_TOPICS:
        score += 1
        reasons.append("admissions topic match")
    if source_type in {"official", "blog"}:
        score += 1
        reasons.append(f"{source_type} source")

    engagement_score = _as_int(metadata.get("score"))
    if source_type == "reddit" and engagement_score >= 50:
        score += 1
        reasons.append("high Reddit engagement")

    final_score = max(1, min(score, 5))
    why = ", ".join(reasons) if reasons else "limited signal but retained as collected content"
    return final_score, why


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
