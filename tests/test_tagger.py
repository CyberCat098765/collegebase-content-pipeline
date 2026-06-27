from src.processing.tagger import (
    infer_audience_tags,
    infer_content_use,
    infer_topic_tags,
)


def test_infer_topic_tags_matches_admissions_terms() -> None:
    tags = infer_topic_tags("My senior is writing Common App essays for early decision.")

    assert "college_essays" in tags
    assert "early_admissions" in tags
    assert "common_app" in tags


def test_infer_audience_tags_defaults_to_general() -> None:
    assert infer_audience_tags("How should applicants think about financial aid?") == [
        "general"
    ]


def test_infer_content_use_for_official_data() -> None:
    assert infer_content_use("Admission rate: 0.05", "official", ["admissions_strategy"]) == (
        "chatbot_answer"
    )
