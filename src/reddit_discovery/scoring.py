from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from src.reddit_discovery.constants import TOPIC_KEYWORDS, TOPIC_TAXONOMY
from src.reddit_discovery.filtering import is_curated_resource

COMPONENT_CAPS = {
    "content_depth": 20,
    "actionability": 20,
    "topic_relevance": 20,
    "credibility_signals": 15,
    "durability": 15,
    "engagement_signal": 10,
}
@dataclass(frozen=True, slots=True)
class ScoreComponent:
    score: int
    max_score: int
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {"score": self.score, "max_score": self.max_score, "reasons": list(self.reasons)}

@dataclass(frozen=True, slots=True)
class TopicAssignment:
    primary_topic: str
    secondary_topics: tuple[str, ...]
    match_scores: dict[str, int]

@dataclass(frozen=True, slots=True)
class HeuristicScore:
    total: int
    components: dict[str, ScoreComponent]

    @property
    def scores(self) -> dict[str, int]:
        return {name: component.score for name, component in self.components.items()}

    @property
    def breakdown(self) -> dict[str, dict[str, object]]:
        return {name: component.to_dict() for name, component in self.components.items()}

@dataclass(frozen=True, slots=True)
class HeuristicEnrichment:
    primary_topic: str
    secondary_topics: tuple[str, ...]
    audience: tuple[str, ...]
    summary: str
    key_takeaways: tuple[str, ...]
    why_useful: str
    limitations_or_cautions: tuple[str, ...]
    freshness_status: str
    confidence: float
    selection_reasons: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class ClassificationDecision:
    status: str
    reason_code: str
    selection_reasons: tuple[str, ...] = ()

def score_candidate(candidate: object) -> HeuristicScore:
    text = _candidate_text(candidate)
    assignment = assign_topics(_text(_field(candidate, "title")), _text(_field(candidate, "selftext")))
    components = {
        "content_depth": _score_content_depth(_text(_field(candidate, "selftext"))),
        "actionability": _score_actionability(text),
        "topic_relevance": _score_topic_relevance(text, assignment),
        "credibility_signals": _score_credibility(candidate, text),
        "durability": _score_durability(text),
        "engagement_signal": _score_engagement(candidate),
    }
    total = sum(component.score for component in components.values())
    return HeuristicScore(max(0, min(100, total)), components)

def assign_topics(title: str, body: str) -> TopicAssignment:
    text = _normalize(f"{title}\n{body}")
    scores = {
        topic: sum(_contains(text, keyword) for keyword in keywords)
        for topic, keywords in TOPIC_KEYWORDS.items()
    }
    scores = {topic: score for topic, score in scores.items() if score > 0}
    if not scores and _has_general_application_context(text):
        scores = {"general_application": 1}
    if not scores:
        return TopicAssignment("other", (), {})
    order = {topic: index for index, topic in enumerate(TOPIC_TAXONOMY)}
    ranked = sorted(scores, key=lambda topic: (-scores[topic], order[topic]))
    return TopicAssignment(ranked[0], tuple(ranked[1:3]), scores)

def build_heuristic_enrichment(candidate: object, score: HeuristicScore) -> HeuristicEnrichment:
    title = _text(_field(candidate, "title"))
    body = _text(_field(candidate, "selftext"))
    topics = assign_topics(title, body)
    freshness = infer_freshness_status(f"{title}\n{body}")
    summary = deterministic_summary(title, body)
    takeaways = deterministic_takeaways(title, body)
    strongest = sorted(score.components, key=lambda name: score.components[name].score, reverse=True)[:3]
    reasons = tuple(
        f"{name}:{score.components[name].score}/{score.components[name].max_score}"
        for name in strongest
        if score.components[name].score > 0
    )
    limitations = ["Community-generated Reddit content; verify claims against current official guidance."]
    if freshness != "durable":
        limitations.append("Time-sensitive details may no longer match the current admissions cycle.")
    if score.components["credibility_signals"].score < 5:
        limitations.append("The post has limited explicit sourcing or moderator verification.")
    why = (
        f"Provides reusable {topics.primary_topic.replace('_', ' ')} guidance"
        f" with a {score.components['actionability'].score}/20 actionability score."
    )
    confidence = round(min(0.88, 0.50 + score.total / 250), 2)
    return HeuristicEnrichment(
        primary_topic=topics.primary_topic,
        secondary_topics=topics.secondary_topics,
        audience=tuple(infer_audience(f"{title}\n{body}")),
        summary=summary,
        key_takeaways=tuple(takeaways),
        why_useful=why,
        limitations_or_cautions=tuple(limitations),
        freshness_status=freshness,
        confidence=confidence,
        selection_reasons=reasons,
    )

def apply_heuristic_evaluation(candidate: object) -> tuple[HeuristicScore, HeuristicEnrichment]:
    score = score_candidate(candidate)
    enrichment = build_heuristic_enrichment(candidate, score)
    assignments = {
        "primary_topic": enrichment.primary_topic,
        "secondary_topics": list(enrichment.secondary_topics),
        "audience": list(enrichment.audience),
        "summary": enrichment.summary,
        "key_takeaways": list(enrichment.key_takeaways),
        "why_useful": enrichment.why_useful,
        "limitations_or_cautions": list(enrichment.limitations_or_cautions),
        "freshness_status": enrichment.freshness_status,
        "heuristic_score": score.total,
        "final_usefulness_score": score.total,
        "score_breakdown": score.breakdown,
        "confidence": enrichment.confidence,
        "selection_reasons": list(enrichment.selection_reasons),
    }
    for name, value in assignments.items():
        _set_field(candidate, name, value)
    return score, enrichment


def apply_llm_adjustment(heuristic_score: int, adjustment: int) -> tuple[int, int]:
    bounded_adjustment = max(-15, min(15, int(adjustment)))
    return max(0, min(100, int(heuristic_score) + bounded_adjustment)), bounded_adjustment


def classify_candidate(
    candidate: object,
    *,
    minimum_usefulness_score: int = 70,
    llm_active: bool = False,
    llm_is_reusable: bool | None = None,
    malformed_llm_output: bool = False,
    severe_misinformation_concern: bool = False,
) -> ClassificationDecision:
    hard_reason = _text(_field(candidate, "hard_rejection_reason"))
    if hard_reason:
        return ClassificationDecision("rejected", hard_reason)
    if malformed_llm_output:
        return ClassificationDecision("human_review", "MALFORMED_LLM_OUTPUT")
    if severe_misinformation_concern:
        return ClassificationDecision("human_review", "SEVERE_MISINFORMATION_CONCERN")
    if llm_active and llm_is_reusable is False:
        return ClassificationDecision("rejected", "LLM_NOT_REUSABLE")

    score = _as_int(_field(candidate, "final_usefulness_score"))
    breakdown = _field(candidate, "score_breakdown", {})
    topic_component = (
        breakdown.get("topic_relevance") if isinstance(breakdown, Mapping) else None
    )
    summary = _text(_field(candidate, "summary"))
    takeaways = _string_list(_field(candidate, "key_takeaways", []))
    if score < 60:
        if is_moderator_resource(candidate):
            return ClassificationDecision("human_review", "MODERATOR_RESOURCE_OVERRIDE")
        if score >= 50 and is_curated_resource(candidate):
            return ClassificationDecision("human_review", "CURATED_RESOURCE_REVIEW")
        return ClassificationDecision("rejected", "LOW_USEFULNESS_SCORE")
    if score < 70:
        return ClassificationDecision("human_review", "HUMAN_REVIEW_SCORE_BAND")
    if isinstance(topic_component, Mapping) and (
        _as_int(topic_component.get("score")) <= 0
        or _text(_field(candidate, "primary_topic")).casefold() == "other"
    ):
        if is_moderator_resource(candidate):
            return ClassificationDecision("human_review", "MODERATOR_RESOURCE_OVERRIDE")
        return ClassificationDecision("rejected", "OFF_TOPIC_NO_ADMISSIONS_RELEVANCE")
    credibility = breakdown.get("credibility_signals") if isinstance(breakdown, Mapping) else None
    if (
        _text(_field(candidate, "freshness_status")).casefold() == "time_sensitive"
        and isinstance(credibility, Mapping)
        and _as_int(credibility.get("score")) < 5
    ):
        return ClassificationDecision("human_review", "TIME_SENSITIVE_UNVERIFIED")
    if score >= max(70, minimum_usefulness_score):
        if not summary:
            return ClassificationDecision("human_review", "MISSING_REUSABLE_SUMMARY")
        if llm_active and len(takeaways) < 2:
            return ClassificationDecision("human_review", "INSUFFICIENT_LLM_TAKEAWAYS")
        return ClassificationDecision(
            "accepted",
            "ACCEPTED_USEFUL_RESOURCE",
            tuple(_string_list(_field(candidate, "selection_reasons", []))),
        )
    return ClassificationDecision("human_review", "HUMAN_REVIEW_SCORE_BAND")


def is_moderator_resource(candidate: object) -> bool:
    distinguished = _text(_field(candidate, "distinguished")).casefold() == "moderator"
    stickied = bool(_field(candidate, "stickied", False))
    title = _normalize(_text(_field(candidate, "title")))
    return (distinguished or stickied) and any(
        _contains(title, marker) for marker in ("guide", "resource", "faq", "megathread", "ama")
    )


def infer_freshness_status(text: str) -> str:
    normalized = _normalize(text)
    has_year = bool(re.search(r"\b20\d{2}\b", normalized))
    historical = any(term in normalized for term in ("historical", "archive", "in retrospect"))
    temporary = any(
        term in normalized
        for term in ("this cycle", "current cycle", "this year", "policy change", "today", "currently")
    )
    if has_year and historical:
        return "historical"
    year_is_cycle_specific = has_year and any(
        term in normalized for term in ("cycle", "deadline", "policy", "result", "decision date")
    )
    if temporary or year_is_cycle_specific:
        return "time_sensitive"
    return "durable"


def infer_audience(text: str) -> list[str]:
    normalized = _normalize(text)
    audience = ["high_school_students"]
    if any(term in normalized for term in ("parent", "guardian", "family", "families")):
        audience.append("parents_and_guardians")
    if "international" in normalized:
        audience.append("international_students")
    if "transfer" in normalized:
        audience.append("transfer_students")
    if any(term in normalized for term in ("first generation", "first gen", "low income", "questbridge")):
        audience.append("first_generation_students")
    return audience


def deterministic_summary(title: str, body: str, max_chars: int = 500) -> str:
    sentences = _substantive_sentences(body)
    selected = sentences[:2]
    if selected:
        return _truncate(" ".join(selected), max_chars)
    return _truncate(title.strip(), max_chars)


def deterministic_takeaways(title: str, body: str, limit: int = 3) -> list[str]:
    sentences = _substantive_sentences(body)
    action_terms = (
        "should", "must", "start", "avoid", "check", "compare", "ask", "submit",
        "write", "include", "review", "step", "deadline", "example",
    )
    ranked = sorted(
        enumerate(sentences),
        key=lambda pair: (-sum(term in pair[1].casefold() for term in action_terms), pair[0]),
    )
    chosen_indices = sorted(index for index, _ in ranked[:limit])
    takeaways = [_truncate(sentences[index], 260) for index in chosen_indices]
    return takeaways or ([_truncate(title, 260)] if title.strip() else [])


def _score_content_depth(body: str) -> ScoreComponent:
    words = len(body.split())
    score, reasons = 0, []
    for threshold, points in ((80, 4), (180, 4), (350, 4), (700, 2)):
        if words >= threshold:
            score += points
            reasons.append(f"at least {threshold} words")
    if len([part for part in re.split(r"\n\s*\n|\n", body) if len(part.split()) >= 12]) >= 3:
        score += 2
        reasons.append("multiple substantive sections")
    if re.search(r"(?m)^\s{0,3}(?:#{1,6}\s+|[A-Z][^\n]{2,60}:\s*$)", body):
        score += 2
        reasons.append("headings")
    if len(re.findall(r"(?m)^\s*(?:[-*\u2022]|\d+[.)])\s+", body)) >= 3:
        score += 2
        reasons.append("structured list")
    sentences = [_normalize(sentence) for sentence in _substantive_sentences(body)]
    if len(sentences) >= 6 and len(set(sentences)) / len(sentences) < 0.5:
        score -= 4
        reasons.append("repetitive text penalty")
    return _component("content_depth", score, reasons)


def _score_actionability(text: str) -> ScoreComponent:
    groups = (
        (4, ("step", "first", "next", "then"), "concrete steps"),
        (4, ("checklist", "to do", "bullet point"), "checklist or list"),
        (3, ("example", "for instance", "sample"), "examples"),
        (3, ("avoid", "warning", "mistake", "watch out"), "warnings"),
        (3, ("timeline", "deadline", "month by month"), "timeline"),
        (3, ("template", "worksheet", "script"), "template"),
        (3, ("compare", "tradeoff", "option", "choose"), "explained choices"),
    )
    normalized = _normalize(text)
    score, reasons = 0, []
    for points, markers, reason in groups:
        if any(_contains(normalized, marker) for marker in markers):
            score += points
            reasons.append(reason)
    if len(re.findall(r"(?m)^\s*(?:[-*\u2022]|\d+[.)])\s+", text)) >= 3 and "checklist or list" not in reasons:
        score += 4
        reasons.append("checklist or list")
    return _component("actionability", score, reasons)


def _score_topic_relevance(text: str, assignment: TopicAssignment) -> ScoreComponent:
    if assignment.primary_topic == "other":
        return _component("topic_relevance", 0, ["no controlled-topic match"])
    strongest = max(assignment.match_scores.values(), default=0)
    score = 10 if strongest == 1 else 14 if strongest == 2 else 17
    reasons = [f"primary topic: {assignment.primary_topic}", f"{strongest} primary-topic signals"]
    if len(assignment.match_scores) >= 2:
        score += 1
        reasons.append("multiple relevant topics")
    normalized = _normalize(text)
    if any(_contains(normalized, term) for term in ("college application", "college admissions", "applicant")):
        score += 2
        reasons.append("explicit college-admissions context")
    return _component("topic_relevance", score, reasons)


def _score_credibility(candidate: object, text: str) -> ScoreComponent:
    score, reasons = 0, []
    if _text(_field(candidate, "distinguished")).casefold() == "moderator":
        score += 5
        reasons.append("moderator distinguished")
    if bool(_field(candidate, "stickied", False)):
        score += 3
        reasons.append("stickied resource")
    external_url = _text(_field(candidate, "external_url")) or _text(_field(candidate, "canonical_url"))
    host = (urlparse(external_url).hostname or "").casefold()
    if _is_official_host(host):
        score += 3
        reasons.append("linked official resource")
    normalized = _normalize(text)
    if "http" in text.casefold() or any(term in normalized for term in ("according to", "source", "citation")):
        score += 2
        reasons.append("explicit sourcing")
    if any(term in normalized for term in ("may vary", "not a guarantee", "check current", "depends on")):
        score += 2
        reasons.append("states limitations")
    if any(term in normalized for term in ("guaranteed acceptance", "guaranteed admission")):
        score -= 3
        reasons.append("unsupported guarantee language")
    return _component("credibility_signals", score, reasons)


def _score_durability(text: str) -> ScoreComponent:
    freshness = infer_freshness_status(text)
    if freshness == "durable":
        return _component("durability", 15, ["not tied to a specific admissions cycle"])
    if freshness == "historical":
        return _component("durability", 8, ["historical context has limited current applicability"])
    return _component("durability", 6, ["contains cycle-specific or temporary details"])


def _score_engagement(candidate: object) -> ScoreComponent:
    reddit_score = max(0, _as_int(_field(candidate, "score")))
    base = min(10, round(math.log1p(reddit_score) * 1.4))
    score, reasons = base, [f"bounded log engagement from score {reddit_score}"]
    comments = max(0, _as_int(_field(candidate, "num_comments")))
    if comments >= 100:
        score += 2
        reasons.append("100+ comments")
    elif comments >= 25:
        score += 1
        reasons.append("25+ comments")
    ratio = _as_float(_field(candidate, "upvote_ratio", None))
    if ratio is not None and ratio >= 0.90:
        score += 1
        reasons.append("high upvote ratio")
    elif ratio is not None and ratio < 0.50:
        score -= 1
        reasons.append("low upvote ratio")
    return _component("engagement_signal", score, reasons)


def _component(name: str, score: int, reasons: list[str]) -> ScoreComponent:
    maximum = COMPONENT_CAPS[name]
    return ScoreComponent(max(0, min(maximum, score)), maximum, tuple(reasons))


def _candidate_text(candidate: object) -> str:
    return f"{_text(_field(candidate, 'title'))}\n{_text(_field(candidate, 'selftext'))}".strip()


def _substantive_sentences(body: str) -> list[str]:
    compact = re.sub(r"(?m)^\s*(?:[-*\u2022]|\d+[.)])\s+", "", body.strip())
    sentences = re.split(r"(?<=[.!?])\s+|\n+", compact)
    return [re.sub(r"\s+", " ", sentence).strip() for sentence in sentences if len(sentence.split()) >= 8]


def _is_official_host(host: str) -> bool:
    return host.endswith((".gov", ".edu")) or host in {
        "commonapp.org", "www.commonapp.org", "studentaid.gov", "collegeboard.org",
        "www.collegeboard.org", "act.org", "www.act.org",
    }


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.casefold())).strip()


def _contains(normalized_text: str, phrase: str) -> bool:
    normalized_phrase = _normalize(phrase)
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(normalized_phrase)}(?![a-z0-9])", normalized_text))


def _has_general_application_context(normalized_text: str) -> bool:
    if any(
        _contains(normalized_text, phrase)
        for phrase in ("college application", "college admissions", "applying to college")
    ):
        return True
    applicant = _contains(normalized_text, "applicant") or _contains(
        normalized_text, "applicants"
    )
    anchored = any(
        _contains(normalized_text, phrase)
        for phrase in ("admissions", "college", "counselor", "essay", "financial aid")
    )
    workflow_signals = sum(
        _contains(normalized_text, phrase)
        for phrase in ("application", "deadline", "submission", "recommendation", "forms")
    )
    return applicant and anchored and workflow_signals >= 2


def _truncate(value: str, max_chars: int) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:") + "..."


def _field(candidate: object, name: str, default: Any = "") -> Any:
    if isinstance(candidate, Mapping):
        return candidate.get(name, default)
    return getattr(candidate, name, default)


def _set_field(candidate: object, name: str, value: object) -> None:
    if isinstance(candidate, dict):
        candidate[name] = value
    else:
        setattr(candidate, name, value)


def _text(value: object) -> str:
    return str(value or "").strip()


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item)]
