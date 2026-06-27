from src.models import SourceItem
from src.processing.admissions_filter import evaluate_admissions_relevance
from src.processing.pipeline import process_items


def test_college_essay_content_is_kept() -> None:
    relevance = evaluate_admissions_relevance(
        "A strong college essay should use specific details from the applicant's life."
    )

    assert relevance.score >= 3
    assert "college_essays" in relevance.topics
    assert relevance.drop_reason == ""


def test_financial_aid_content_is_kept() -> None:
    relevance = evaluate_admissions_relevance(
        "Families should complete the FAFSA and compare financial aid offers before choosing a college."
    )

    assert relevance.score >= 3
    assert "financial_aid" in relevance.topics


def test_common_app_content_is_kept() -> None:
    relevance = evaluate_admissions_relevance(
        "The Common App activities list should make each activity's role and impact clear."
    )

    assert relevance.score >= 3
    assert "common_app" in relevance.topics
    assert "activities_list" in relevance.topics


def test_dorm_party_lifestyle_content_is_dropped() -> None:
    relevance = evaluate_admissions_relevance(
        "This dorm room guide covers party outfits, roommate drama, dining hall food, and nightlife."
    )

    assert relevance.score < 3
    assert relevance.topics == []
    assert "dorm" in relevance.drop_reason or "lifestyle" in relevance.drop_reason


def test_unrelated_news_is_dropped() -> None:
    relevance = evaluate_admissions_relevance(
        "The school board election campaign focused on a local budget fight."
    )

    assert relevance.score < 3
    assert relevance.topics == []
    assert relevance.drop_reason


def test_source_is_dropped_when_most_chunks_are_irrelevant() -> None:
    item = SourceItem(
        source_type="blog",
        source_name="example.com",
        title="Mixed Article",
        url="https://example.com/mixed",
        author_or_channel="",
        published_date="",
        collected_at="2026-06-27T00:00:00Z",
        raw_text=(
            "A college essay should connect a specific story to what the applicant values. "
            "Admissions officers need concrete evidence, reflection, and a clear reason the "
            "story belongs in the application instead of a generic list of traits.\n\n"
            "Dorm room shopping lists often include lamps, bedding, posters, storage bins, "
            "laundry bags, snack carts, and roommate decoration plans. The article focuses on "
            "move-in style, campus food, parties, and nightlife after admission.\n\n"
            "Weekend party guides usually cover nightlife, roommate plans, dining hall tips, "
            "fraternity events, outfit ideas, and social routines. This kind of lifestyle "
            "content is not about applying to college or the admissions process."
        ),
    )

    processed, errors, stats = process_items([item], max_chars=230)

    assert processed == []
    assert stats["sources_dropped_by_admissions_filter"] == 1
    assert errors[0].source == "https://example.com/mixed"
    assert "Dropped by admissions relevance filter" in errors[0].message
