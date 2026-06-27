from __future__ import annotations

import re


TOPIC_RULES: dict[str, tuple[str, ...]] = {
    "college_essays": ("essay", "essays", "personal statement", "supplemental"),
    "early_admissions": ("early decision", "early action", "ed1", "ed2", "rea"),
    "financial_aid": ("financial aid", "fafsa", "css profile", "scholarship", "aid"),
    "common_app": ("common app", "activities list", "activity list", "additional info"),
    "admissions_strategy": ("admission", "application", "acceptance", "reject", "defer"),
    "standardized_testing": ("sat", "act", "test optional", "ap score"),
    "recommendations": ("recommendation", "rec letter", "teacher rec", "counselor"),
    "college_list": ("school list", "college list", "target school", "safety school"),
    "interviews": ("interview", "alumni interview"),
    "transfer": ("transfer", "community college"),
}

AUDIENCE_RULES: dict[str, tuple[str, ...]] = {
    "freshman": ("freshman", "9th grade", "ninth grade"),
    "sophomore": ("sophomore", "10th grade", "tenth grade"),
    "junior": ("junior", "11th grade", "eleventh grade"),
    "senior": ("senior", "12th grade", "twelfth grade"),
    "parent": ("parent", "guardian", "my kid", "my child", "daughter", "son"),
}


def infer_topic_tags(text: str) -> list[str]:
    lowered = text.lower()
    tags = [
        tag
        for tag, keywords in TOPIC_RULES.items()
        if any(_contains_keyword(lowered, keyword) for keyword in keywords)
    ]
    return tags or ["general_admissions"]


def infer_audience_tags(text: str) -> list[str]:
    lowered = text.lower()
    tags = [
        audience
        for audience, keywords in AUDIENCE_RULES.items()
        if any(_contains_keyword(lowered, keyword) for keyword in keywords)
    ]
    return tags or ["general"]


def infer_content_use(text: str, source_type: str, topic_tags: list[str]) -> str:
    word_count = len(text.split())
    if source_type == "official":
        return "chatbot_answer"
    if source_type == "youtube":
        return "both" if word_count >= 80 else "video_script"
    if word_count >= 120 and topic_tags != ["general_admissions"]:
        return "both"
    return "chatbot_answer"


def _contains_keyword(text: str, keyword: str) -> bool:
    pattern = rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])"
    return re.search(pattern, text) is not None
