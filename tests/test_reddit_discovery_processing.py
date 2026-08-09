from __future__ import annotations

from typing import Any

import pytest

from src.reddit_discovery.constants import TOPIC_TAXONOMY
from src.reddit_discovery.dedupe import (
    DUPLICATE_CANONICAL_URL,
    DUPLICATE_POST_ID,
    NEAR_DUPLICATE_LOWER_QUALITY,
    choose_representative,
    cluster_near_duplicates,
    deduplicate_candidates,
    normalize_title,
    normalized_title_similarity,
)
from src.reddit_discovery.filtering import evaluate_hard_filters
from src.reddit_discovery.models import RedditCandidate
from src.reddit_discovery.scoring import (
    COMPONENT_CAPS,
    apply_heuristic_evaluation,
    classify_candidate,
)
from src.reddit_discovery.title_index import blocked_title_pairs, title_similarity_edges


@pytest.mark.parametrize(
    ("title", "body", "overrides", "reason_code"),
    [
        ("Removed resource", "[removed]", {}, "REMOVED_CONTENT"),
        ("Deleted resource", "[deleted]", {}, "DELETED_CONTENT"),
        (
            "Deleted by author",
            "Content that Reddit no longer makes available.",
            {"removed_by_category": "deleted"},
            "DELETED_CONTENT",
        ),
        (
            "Reverse Chance Me for selective colleges",
            "Here are my individual statistics. " * 20,
            {},
            "BLOCKED_CATEGORY_CHANCE_ME",
        ),
        (
            "College Results 2026",
            "This post lists individual outcomes without reusable guidance. " * 12,
            {},
            "BLOCKED_CATEGORY_RESULTS",
        ),
        ("Admissions meme", "A reaction image with no reusable advice. " * 15, {}, "BLOCKED_CATEGORY_MEME"),
        (
            "Manifestation and hype thread",
            "A reaction thread with no reusable advice. " * 15,
            {},
            "BLOCKED_CATEGORY_FLUFF",
        ),
        (
            "Can I get into Yale?",
            "Here are my grades and activities; please predict my personal outcome. " * 12,
            {},
            "NARROW_PERSONAL_QUESTION",
        ),
        (
            "Is 1450 SAT good?",
            "I want strangers to answer one personal score question.",
            {},
            "TRIVIAL_TEST_SCORE_QUESTION",
        ),
        (
            "OMG I just got accepted!!!",
            "A personal celebration without reusable admissions guidance.",
            {},
            "BLOCKED_CATEGORY_CELEBRATION",
        ),
        (
            "My admissions editing guide",
            "Buy my course and book a consultation for my paid service.",
            {},
            "BLOCKED_CATEGORY_SELF_PROMOTION",
        ),
    ],
)
def test_hard_rejection_rules(
    title: str,
    body: str,
    overrides: dict[str, Any],
    reason_code: str,
) -> None:
    candidate = _candidate(title=title, body=body, **overrides)

    decision = evaluate_hard_filters(candidate)

    assert decision.rejected is True
    assert decision.reason_code == reason_code
    assert decision.explanation


def test_blocked_phrase_in_substantive_body_does_not_reject_guide() -> None:
    candidate = _candidate(
        title="Detailed personal statement guide",
        body=(
            "This reusable guide explains why applicants should avoid turning an essay "
            "review into a chance me discussion. It gives concrete revision steps, examples, "
            "and limitations for future applicants. "
        )
        * 8,
    )

    decision = evaluate_hard_filters(candidate)

    assert decision.passed is True
    assert decision.reason_code is None


@pytest.mark.parametrize(
    ("title", "topic_text", "external_url", "expected_topic"),
    [
        (
            "Comprehensive college essay guide",
            (
                "personal statement Common App essay college essay guide essay advice "
                "essay mistakes college application"
            ),
            "https://www.commonapp.org/apply/essay-prompts",
            "personal_essay",
        ),
        (
            "Financial aid guide and FAFSA checklist",
            (
                "financial aid FAFSA CSS Profile net price calculator financial aid appeal "
                "need based aid merit aid college application"
            ),
            "https://studentaid.gov/complete-aid-process",
            "financial_aid",
        ),
    ],
)
def test_detailed_guides_score_at_least_70_with_exact_breakdown(
    title: str,
    topic_text: str,
    external_url: str,
    expected_topic: str,
) -> None:
    candidate = _candidate(
        title=title,
        body=_structured_guide(topic_text),
        external_url=external_url,
        is_self=False,
        score=180,
        num_comments=45,
        upvote_ratio=0.96,
    )
    assert evaluate_hard_filters(candidate).passed

    score, enrichment = apply_heuristic_evaluation(candidate)

    assert score.total >= 70
    assert set(score.components) == set(COMPONENT_CAPS)
    assert set(candidate.score_breakdown) == set(COMPONENT_CAPS)
    assert score.total == sum(component.score for component in score.components.values())
    for name, maximum in COMPONENT_CAPS.items():
        component = candidate.score_breakdown[name]
        assert component["score"] == score.components[name].score
        assert component["max_score"] == maximum
        assert 0 <= component["score"] <= maximum
        assert component["reasons"]
    assert candidate.primary_topic == expected_topic
    assert candidate.primary_topic in TOPIC_TAXONOMY
    assert set(candidate.secondary_topics) <= set(TOPIC_TAXONOMY)
    assert len(candidate.secondary_topics) <= 2
    assert enrichment.summary == candidate.summary


def test_moderator_megathread_is_retained_by_override() -> None:
    candidate = _candidate(
        title="Moderator FAQ Megathread",
        body="Short official-resource index.",
        stickied=True,
        distinguished="moderator",
    )

    assert evaluate_hard_filters(candidate).passed
    apply_heuristic_evaluation(candidate)
    candidate.final_usefulness_score = min(candidate.final_usefulness_score, 59)
    decision = classify_candidate(candidate)

    assert decision.status == "human_review"
    assert decision.reason_code == "MODERATOR_RESOURCE_OVERRIDE"


def test_no_llm_enrichment_is_nonempty_and_deterministic() -> None:
    first = _candidate(body=_structured_guide("personal statement college essay guide"))
    second = _candidate(body=first.selftext)

    first_score, first_enrichment = apply_heuristic_evaluation(first)
    second_score, second_enrichment = apply_heuristic_evaluation(second)

    assert first_score.total == second_score.total
    assert first_enrichment == second_enrichment
    assert first.summary
    assert len(first.key_takeaways) >= 2
    assert first.why_useful
    assert first.audience == ["high_school_students"]
    assert first.llm_adjustment == 0
    assert first.final_usefulness_score == first.heuristic_score


@pytest.mark.parametrize(
    ("score", "expected_status", "expected_reason"),
    [
        (70, "accepted", "ACCEPTED_USEFUL_RESOURCE"),
        (69, "human_review", "HUMAN_REVIEW_SCORE_BAND"),
        (60, "human_review", "HUMAN_REVIEW_SCORE_BAND"),
        (59, "rejected", "LOW_USEFULNESS_SCORE"),
    ],
)
def test_classification_score_bands(
    score: int,
    expected_status: str,
    expected_reason: str,
) -> None:
    candidate = _candidate()
    candidate.final_usefulness_score = score
    candidate.summary = "A reusable summary."
    candidate.key_takeaways = ["First useful point.", "Second useful point."]

    decision = classify_candidate(candidate)

    assert decision.status == expected_status
    assert decision.reason_code == expected_reason


def test_malformed_llm_output_fails_to_safe_human_review() -> None:
    candidate = _candidate()
    candidate.final_usefulness_score = 95
    candidate.summary = "A reusable summary."
    candidate.key_takeaways = ["First useful point.", "Second useful point."]

    decision = classify_candidate(
        candidate,
        llm_active=True,
        malformed_llm_output=True,
    )

    assert decision.status == "human_review"
    assert decision.reason_code == "MALFORMED_LLM_OUTPUT"


def test_time_sensitive_unsourced_guidance_requires_human_review() -> None:
    candidate = _candidate(
        title="2026 application deadline guide",
        body=_structured_guide("college application deadline for this cycle"),
    )
    apply_heuristic_evaluation(candidate)
    candidate.final_usefulness_score = 90
    candidate.score_breakdown["credibility_signals"]["score"] = 0

    decision = classify_candidate(candidate)

    assert candidate.freshness_status == "time_sensitive"
    assert decision.status == "human_review"
    assert decision.reason_code == "TIME_SENSITIVE_UNVERIFIED"


def test_title_normalization_removes_prefixes_punctuation_and_cycle_years() -> None:
    assert normalize_title("PSA: Guide — Essay Plan 2024-25!") == "essay plan"
    assert normalized_title_similarity(
        "Guide: Essay Plan 2024",
        "PSA Essay Plan 2025",
    ) == 1.0


def test_large_title_blocker_keeps_a_bounded_candidate_graph() -> None:
    titles = [
        normalize_title(f"Advice sequence for application item {index:05d}")
        for index in range(400)
    ]

    pairs = blocked_title_pairs(titles)

    assert len(pairs) <= len(titles) * 32
    assert len(pairs) < len(titles) * (len(titles) - 1) // 2


def test_large_title_index_preserves_order_sensitive_duplicate_edge() -> None:
    import random

    ordered = (
        "admissions application essay activities honors counselor recommendation "
        "financial aid scholarship interview deadline checklist timeline college "
        "applicant submission common supplemental testing transfer"
    ).split()
    titles = [" ".join([*ordered, "alpha"]), " ".join([*ordered, "beta"])]
    for suffix, offset in (("alpha", 0), ("beta", 100)):
        for index in range(60):
            shuffled = list(ordered)
            random.Random(offset + index).shuffle(shuffled)
            titles.append(" ".join([*shuffled, suffix]))

    edges = title_similarity_edges(titles, 0.90)
    adjacency: dict[int, set[int]] = {index: set() for index in range(len(titles))}
    for left, right, _ in edges:
        adjacency[left].add(right)
        adjacency[right].add(left)
    reachable, pending = {0}, [0]
    while pending:
        pending.extend(adjacency[pending.pop()] - reachable)
        reachable.update(pending)

    assert normalized_title_similarity(titles[0], titles[1]) >= 0.90
    assert 1 in reachable


def test_real_tfidf_clusters_near_duplicates_and_preserves_pointer() -> None:
    shared = (
        "Applicants should build a checklist, compare requirements, collect examples, "
        "and verify details with official sources. A personal statement should use "
        "specific evidence, reflection, and a clear explanation of why it matters. "
    ) * 18
    weaker = _candidate(
        "tfidf-a",
        title="Long-form writing handbook",
        body=shared + "Alpha ending.",
        final_usefulness_score=74,
    )
    stronger = _candidate(
        "tfidf-b",
        title="Practical application writing resource",
        body=shared + "Beta ending.",
        final_usefulness_score=86,
    )
    distinct = _candidate(
        "tfidf-c",
        title="Financial aid appeal question",
        body=("FAFSA CSS Profile net price calculator appeal documentation. " * 18),
        final_usefulness_score=80,
    )

    result = cluster_near_duplicates([weaker, stronger, distinct])

    assert [candidate.reddit_post_id for candidate in result.retained] == ["tfidf-b", "tfidf-c"]
    assert len(result.duplicates) == 1
    duplicate = result.duplicates[0]
    assert duplicate.reason_code == NEAR_DUPLICATE_LOWER_QUALITY
    assert duplicate.duplicate_of_resource_id == "reddit_tfidf-b"
    assert duplicate.candidate.duplicate_of == "reddit_tfidf-b"
    assert duplicate.candidate.processing_status == "rejected"
    assert result.clusters[0].match_reasons == ("tfidf_text",)
    assert result.clusters[0].retained_resource_id == "reddit_tfidf-b"


def test_strongest_representative_uses_required_precedence() -> None:
    plain = _candidate("plain", final_usefulness_score=100)
    moderator = _candidate(
        "moderator",
        distinguished="moderator",
        final_usefulness_score=1,
    )
    assert choose_representative([plain, moderator]) is moderator

    stickied = _candidate("stickied", stickied=True, final_usefulness_score=1)
    assert choose_representative([plain, stickied]) is stickied

    lower_score = _candidate("lower-score", final_usefulness_score=70)
    higher_score = _candidate("higher-score", final_usefulness_score=71)
    assert choose_representative([lower_score, higher_score]) is higher_score

    older = _candidate(
        "older",
        created_utc=100,
        freshness_status="time_sensitive",
        resource_id="reddit_a",
    )
    newer = _candidate(
        "newer",
        created_utc=200,
        freshness_status="time_sensitive",
        resource_id="reddit_z",
    )
    assert choose_representative([older, newer]) is newer

    deeper = _candidate(
        "deeper",
        resource_id="reddit_z",
        score_breakdown=_breakdown(content_depth=15, engagement=2),
    )
    shallower = _candidate(
        "shallower",
        resource_id="reddit_a",
        score_breakdown=_breakdown(content_depth=14, engagement=10),
    )
    assert choose_representative([shallower, deeper]) is deeper

    engaged = _candidate(
        "engaged",
        resource_id="reddit_z",
        score_breakdown=_breakdown(content_depth=15, engagement=8),
    )
    less_engaged = _candidate(
        "less-engaged",
        resource_id="reddit_a",
        score_breakdown=_breakdown(content_depth=15, engagement=7),
    )
    assert choose_representative([less_engaged, engaged]) is engaged


def test_exact_duplicate_records_point_to_retained_resource() -> None:
    first_id = _candidate(
        "same-id",
        title="Essay planning resource",
        body="personal statement reflection evidence revision " * 30,
        discovered_by=["top:all"],
    )
    second_id = _candidate(
        "same-id",
        title="Essay planning resource",
        body=first_id.selftext,
        discovered_by=["new"],
        final_usefulness_score=80,
    )
    first_url = _candidate(
        "url-one",
        title="FAFSA documentation",
        body="FAFSA CSS Profile net price calculator financial aid appeal " * 25,
        external_url="https://example.edu/aid?utm_source=reddit",
    )
    second_url = _candidate(
        "url-two",
        title="Transfer planning",
        body="community college transfer articulation credits transcript planning " * 25,
        external_url="http://example.edu/aid/",
    )

    result = deduplicate_candidates([first_id, second_id, first_url, second_url])
    records = {record.reason_code: record for record in result.duplicates}

    assert {DUPLICATE_POST_ID, DUPLICATE_CANONICAL_URL} <= set(records)
    id_representative = next(
        candidate for candidate in result.retained if candidate.reddit_post_id == "same-id"
    )
    assert id_representative.discovered_by == ["top:all", "new"]
    for record in records.values():
        assert record.duplicate_of_resource_id
        assert record.candidate.duplicate_of == record.duplicate_of_resource_id
        assert record.candidate.rejection_reason == record.reason_code
        assert record.candidate.processing_status == "rejected"
    assert all(cluster.retained_resource_id for cluster in result.clusters)
    assert all(cluster.member_resource_ids for cluster in result.clusters)


def _candidate(
    post_id: str = "post",
    *,
    title: str = "Detailed college essay guide",
    body: str = "Reusable college application guidance with concrete examples. " * 10,
    **overrides: Any,
) -> RedditCandidate:
    values: dict[str, Any] = {
        "reddit_post_id": post_id,
        "fullname": f"t3_{post_id}",
        "subreddit": "ApplyingToCollege",
        "title": title,
        "selftext": body,
        "canonical_url": f"https://www.reddit.com/r/ApplyingToCollege/comments/{post_id}",
        "permalink": f"https://www.reddit.com/r/ApplyingToCollege/comments/{post_id}",
        "author_name": "helpful_user",
        "created_utc": 1_710_000_000.0,
        "retrieved_at": "2026-08-01T00:00:00Z",
        "score": 100,
        "upvote_ratio": 0.94,
        "num_comments": 30,
        "link_flair_text": "Advice",
        "is_self": True,
        "is_original_content": False,
        "over_18": False,
        "spoiler": False,
        "stickied": False,
        "distinguished": None,
        "locked": False,
        "archived": False,
        "removed_by_category": None,
        "discovered_by": ["top:all"],
    }
    values.update(overrides)
    return RedditCandidate(**values)


def _structured_guide(topic_text: str) -> str:
    return (
        f"## Reusable guide\n{topic_text}. This guide explains choices and limitations.\n\n"
        "Step 1: start with a checklist, compare each option, and verify official requirements.\n"
        "- First, collect concrete examples and explain why each one matters.\n"
        "- Next, use the template to draft a clear response with specific evidence.\n"
        "- Then, review the example, avoid common mistakes, and ask for careful feedback.\n\n"
        "For instance, applicants should document a timeline and check every current source. "
        "Policies may vary and this advice is not a guarantee.\n\n"
    ) * 12


def _breakdown(*, content_depth: int, engagement: int) -> dict[str, dict[str, Any]]:
    return {
        "content_depth": {"score": content_depth, "max_score": 20, "reasons": ["fixture"]},
        "engagement_signal": {"score": engagement, "max_score": 10, "reasons": ["fixture"]},
    }
