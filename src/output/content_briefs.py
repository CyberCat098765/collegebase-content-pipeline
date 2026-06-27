from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from src.models import PipelineOutput, SourceItem


TOPIC_LABELS = {
    "college_essays": "college essays",
    "common_app": "Common App",
    "activities_list": "activities list",
    "early_admissions": "early decision",
    "early_decision": "early decision",
    "financial_aid": "financial aid",
    "college_list": "choosing colleges",
    "recommendations": "recommendation letters",
    "recommendation_letters": "recommendation letters",
    "interviews": "interviews",
    "college_interviews": "interviews",
    "application_timeline": "application timeline",
    "demonstrated_interest": "demonstrated interest",
    "admissions_mistakes": "common mistakes",
    "admissions_stats": "admissions stats",
}

KEYWORD_TOPICS = {
    "application timeline": ("deadline", "timeline", "when to apply", "application season"),
    "common mistakes": ("mistake", "avoid", "red flag", "wrong way"),
    "demonstrated interest": ("demonstrated interest", "campus visit", "visit campus"),
}


def build_content_briefs(
    output: PipelineOutput,
    generated_at: str | None = None,
    max_briefs: int = 10,
    max_chunks_per_brief: int = 5,
) -> dict[str, Any]:
    grouped: dict[str, list[tuple[SourceItem, dict[str, Any]]]] = defaultdict(list)

    for item in output.items:
        for chunk in item.chunks:
            chunk_dict = chunk.to_dict()
            if chunk.usefulness_score < 3:
                continue
            if chunk.admissions_relevance_score < 3:
                continue
            for topic in _brief_topics(
                text=chunk.text,
                topic_tags=chunk.topic_tags,
                admissions_topics=chunk.admissions_topics,
            ):
                grouped[topic].append((item, chunk_dict))

    briefs = []
    for topic, pairs in sorted(grouped.items(), key=lambda entry: len(entry[1]), reverse=True):
        selected = sorted(
            pairs,
            key=lambda pair: pair[1].get("usefulness_score", 1),
            reverse=True,
        )[:max_chunks_per_brief]
        source_urls = {item.url for item, _ in selected}
        audiences = sorted(
            {
                audience
                for _, chunk in selected
                for audience in chunk.get("audience", [])
                if audience
            }
        ) or ["general"]

        briefs.append(
            {
                "brief_id": _brief_id(topic, selected),
                "topic": topic,
                "suggested_title": _suggested_title(topic),
                "suggested_video_hook": _suggested_hook(topic),
                "audience": audiences,
                "source_count": len(source_urls),
                "supporting_chunks": [
                    {
                        "chunk_id": str(chunk["chunk_id"]),
                        "source_title": item.title,
                        "source_url": item.url,
                        "citation_label": str(chunk["citation_label"]),
                        "text_preview": _preview(str(chunk["text"])),
                    }
                    for item, chunk in selected
                ],
                "why_this_is_useful": _why_useful(topic, len(selected), len(source_urls)),
            }
        )

        if len(briefs) >= max_briefs:
            break

    return {
        "generated_at": generated_at or output.collected_at,
        "briefs": briefs,
    }


def write_content_briefs(briefs: dict[str, Any], out_path: str | Path) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(briefs, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _brief_topics(
    text: str,
    topic_tags: list[str],
    admissions_topics: list[str],
) -> list[str]:
    topics = [
        TOPIC_LABELS[tag]
        for tag in [*admissions_topics, *topic_tags]
        if tag in TOPIC_LABELS
    ]
    lowered = text.lower()
    for topic, keywords in KEYWORD_TOPICS.items():
        if any(keyword in lowered for keyword in keywords):
            topics.append(topic)
    return _unique(topics or ["general admissions"])


def _brief_id(topic: str, pairs: list[tuple[SourceItem, dict[str, Any]]]) -> str:
    seed = topic + "".join(str(chunk["chunk_id"]) for _, chunk in pairs)
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    return f"brief_{digest[:12]}"


def _suggested_title(topic: str) -> str:
    return {
        "college essays": "What admissions officers look for in college essays",
        "Common App": "How to make the Common App clearer and stronger",
        "activities list": "How to make the Common App activities list clearer",
        "early decision": "What students should know before applying early decision",
        "financial aid": "What families should understand about financial aid",
        "choosing colleges": "How to build a balanced college list",
        "recommendation letters": "How recommendation letters support an application",
        "application timeline": "When students should start each application step",
        "common mistakes": "Common college application mistakes to avoid",
        "demonstrated interest": "What demonstrated interest means in admissions",
        "admissions stats": "How to interpret admissions stats without overreacting",
        "interviews": "How to prepare for college interviews",
    }.get(topic, "Useful college admissions guidance from collected sources")


def _suggested_hook(topic: str) -> str:
    return {
        "college essays": "A strong essay is not about sounding impressive; it is about sounding specific.",
        "Common App": "The Common App works best when each section adds new evidence.",
        "activities list": "The activities list is small, but it can change how the whole application reads.",
        "early decision": "Early decision can help, but only when the student understands the tradeoffs.",
        "financial aid": "The price a college lists is not always the price a family pays.",
        "choosing colleges": "A good college list is built around fit, cost, and realistic admissions odds.",
        "recommendation letters": "A useful recommendation gives admissions readers evidence they cannot see elsewhere.",
        "application timeline": "Most application stress comes from waiting too long to start the right tasks.",
        "common mistakes": "Many application mistakes are avoidable once students know what admissions readers notice.",
        "demonstrated interest": "Some colleges track engagement, but students should not treat it like a gimmick.",
        "admissions stats": "Acceptance rates are useful only when students understand what they do and do not explain.",
        "interviews": "The best interview prep is knowing what stories and questions you want to bring.",
    }.get(topic, "Collected admissions sources point to a practical student question worth answering.")


def _why_useful(topic: str, chunk_count: int, source_count: int) -> str:
    return (
        f"{chunk_count} useful chunks from {source_count} source(s) mention {topic}; "
        "good candidate for chatbot citations or short-form admissions content."
    )


def _preview(text: str, max_chars: int = 260) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars].rsplit(" ", 1)[0] + "..."


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique_values.append(value)
    return unique_values
